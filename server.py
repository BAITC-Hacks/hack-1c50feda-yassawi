"""QamqorNet үшін қарапайым веб-сервер және SQLite дерекқоры."""

from datetime import datetime, timedelta, timezone
from contextlib import contextmanager
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import sqlite3
from urllib.parse import urlparse


PROJECT_DIR = Path(__file__).parent
DATABASE_FILE = PROJECT_DIR / "qamqornet.db"
PORT = 8000


@contextmanager
def connect_to_database():
    """SQLite файлын ашады. Файл жоқ болса, SQLite оны өзі жасайды."""
    connection = sqlite3.connect(DATABASE_FILE)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            yield connection
    finally:
        connection.close()


def initialize_database():
    """Өтінімдер кестесін жасайды және бос кестеге үлгі дерек қосады."""
    with connect_to_database() as connection:
        connection.execute(
            """
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                issue_type TEXT NOT NULL,
                location TEXT NOT NULL,
                operator TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'Жаңа',
                created_at TEXT NOT NULL
            )
            """
        )

        # Алғаш ашқанда ғана мысал өтінімдер енгізіледі. Кейінгі іске қосулар
        # қолданушы қосқан жазбаларды өзгертпейді.
        report_count = connection.execute(
            "SELECT COUNT(*) FROM reports"
        ).fetchone()[0]

        if report_count == 0:
            examples = [
                ("Сигнал әлсіз", "Ясауи көшесі", "Tele2", "Шұғыл", 12),
                ("Интернет баяу", "Отырар шағынауданы", "Beeline", "Бақылауда", 34),
                ("Байланыс үзіліп тұр", "Қала орталығы", "activ", "Тексерілуде", 60),
                ("4G қамту жоқ", "Жаңа құрылыс", "Tele2", "Бақылауда", 120),
            ]
            now = datetime.now(timezone.utc)
            connection.executemany(
                """
                INSERT INTO reports (issue_type, location, operator, status, created_at)
                VALUES (?, ?, ?, ?, ?)
                """,
                [
                    (
                        issue_type,
                        location,
                        operator,
                        status,
                        (now - timedelta(minutes=minutes_ago)).isoformat(timespec="seconds"),
                    )
                    for issue_type, location, operator, status, minutes_ago in examples
                ],
            )


def report_as_dict(row):
    """Дерекқордағы бір жолды JSON-ға ыңғайлы сөздікке айналдырады."""
    return {key: row[key] for key in row.keys()}


class QamqorNetHandler(SimpleHTTPRequestHandler):
    """HTML файлдарын көрсетіп, өтінімдерге арналған шағын API ұсынады."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(PROJECT_DIR), **kwargs)

    def send_json(self, value, status=200):
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if urlparse(self.path).path != "/api/reports":
            return super().do_GET()

        with connect_to_database() as connection:
            rows = connection.execute(
                "SELECT * FROM reports ORDER BY id DESC"
            ).fetchall()
        self.send_json([report_as_dict(row) for row in rows])

    def do_POST(self):
        if urlparse(self.path).path != "/api/reports":
            return self.send_json({"error": "API жол олдсон жок."}, status=404)

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
            if content_length <= 0 or content_length > 10_000:
                return self.send_json({"error": "Өтінім дерегі жарамсыз."}, status=400)

            payload = json.loads(self.rfile.read(content_length))
            if not isinstance(payload, dict):
                return self.send_json({"error": "Өтінім дерегі объект болуы керек."}, status=400)
            issue_type = str(payload.get("issue_type", "")).strip()[:100]
            location = str(payload.get("location", "")).strip()[:150]
            operator = str(payload.get("operator", "")).strip()[:50]
            if not issue_type or not location or not operator:
                return self.send_json({"error": "Барлық жолды толтырыңыз."}, status=400)

            with connect_to_database() as connection:
                cursor = connection.execute(
                    """
                    INSERT INTO reports (issue_type, location, operator, status, created_at)
                    VALUES (?, ?, ?, 'Жаңа', ?)
                    """,
                    (
                        issue_type,
                        location,
                        operator,
                        datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM reports WHERE id = ?", (cursor.lastrowid,)
                ).fetchone()

            self.send_json(report_as_dict(row), status=201)
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "Өтінім JSON пішімінде болуы керек."}, status=400)


if __name__ == "__main__":
    initialize_database()
    server = ThreadingHTTPServer(("127.0.0.1", PORT), QamqorNetHandler)
    print(f"QamqorNet ашылды: http://127.0.0.1:{PORT}")
    print(f"Дерекқор файлы: {DATABASE_FILE}")
    print("Тоқтату үшін Ctrl+C басыңыз.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nСервер зогслоо.")
    finally:
        server.server_close()
