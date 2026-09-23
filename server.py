"""Local-only dashboard. No customer IDs or raw records leave the computer."""
import argparse
import csv
import io
import json
import math
import os
from pathlib import Path
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import pandas as pd
from agent import Agent
from local_eval import evaluate_agent

ROOT = Path(__file__).resolve().parent
os.chdir(ROOT)  # The unchanged organizer scripts resolve CSVs relative to cwd.
LOCK = threading.Lock()
STATE = {'running': False, 'message': 'Дайын', 'error': None, 'result': None}
RUNTIME = ROOT / 'runtime'


def clean(value):
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if hasattr(value, 'item'):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def overview():
    p = pd.read_csv(ROOT / 'customer_profile.csv')
    h = pd.read_csv(ROOT / 'data/change_tariff.csv')
    t = pd.read_csv(ROOT / 'tariff_dictionary.csv').fillna('')
    return clean({'audience': len(p), 'history_rows': len(h), 'baseline': p.predicted_arpu.sum(),
        'tariffs': t.to_dict('records'),
        'segments': [{'name': 'UNKNOWN' if pd.isna(k) else str(k), 'n': len(g), 'arpu': float(g.predicted_arpu.mean())}
                     for k, g in p.groupby('arpu_segment', observed=True, dropna=False)],
        'data_segments': p.data_segment.value_counts().to_dict(),
        'quality': {'missing_required': int(p[['ID_NUMBER','current_tariff','arpu_segment','predicted_arpu']].isna().sum().sum()),
                    'duplicate_ids': int(p.ID_NUMBER.duplicated().sum()),
                    'invalid_arpu': int((~p.predicted_arpu.map(math.isfinite) | (p.predicted_arpu < 0)).sum())},
        'channels': [{'name': 'push', 'cost': 0, 'multiplier': .50},
                     {'name': 'sms', 'cost': 4, 'multiplier': .65},
                     {'name': 'digital_ads', 'cost': 22, 'multiplier': .85},
                     {'name': 'call', 'cost': 160, 'multiplier': 1.20}]})


def progress(message):
    with LOCK:
        STATE['message'] = message


def execute(config):
    try:
        agent = Agent(risk=config['risk'], max_pilots=config['pilots'], progress=progress)
        scored = evaluate_agent(agent, seed=config['seed'], verbose=False)
        if scored is None or not agent.report.get('plan'):
            raise ValueError('Агент жарамды жоспар шығармады. Терминалдағы есепті тексеріңіз.')
        result = clean({'config': config, 'agent': agent.report, 'score': scored,
                        'created_at': time.strftime('%Y-%m-%d %H:%M:%S'), 'stability': []})
        if config['stability']:
            for seed in range(10):
                progress(f'Тұрақтылық сынағы: {seed + 1} / 10')
                r = evaluate_agent(Agent(risk=config['risk'], max_pilots=config['pilots']), seed=seed, verbose=False)
                if r is None:
                    raise ValueError(f'Seed {seed}: бағалау нәтижесі жоқ')
                result['stability'].append(clean({'seed': seed, 'net': r['net_arpu_gain'], 'status': r['status'],
                    'cost': r['total_cost'], 'contacts': r['total_contacts']}))
        RUNTIME.mkdir(exist_ok=True)
        temp = RUNTIME / 'latest.tmp'
        temp.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        temp.replace(RUNTIME / 'latest.json')
        with LOCK:
            STATE.update(result=result, message='Жоспар дайын', error=None)
    except Exception as exc:
        with LOCK:
            STATE.update(error=f'{type(exc).__name__}: {exc}', message='Қате: қайта іске қосып көріңіз')
    finally:
        with LOCK:
            STATE['running'] = False


def parse_config(payload):
    if not isinstance(payload, dict):
        raise ValueError('JSON object required')
    seed, pilots = payload.get('seed', 42), payload.get('pilots', 20)
    risk = payload.get('risk', 1.28)
    stability = payload.get('stability', False)
    if type(seed) is not int or not 0 <= seed <= 2**31 - 1:
        raise ValueError('Seed: 0–2147483647 аралығындағы бүтін сан')
    if type(pilots) is not int or not 1 <= pilots <= 20:
        raise ValueError('Пилот саны: 1–20 аралығындағы бүтін сан')
    if type(risk) not in (int, float) or not math.isfinite(risk) or risk not in (.67, 1.28, 1.64):
        raise ValueError('Тәуекел режимі жарамсыз')
    if type(stability) is not bool:
        raise ValueError('stability must be boolean')
    return {'seed': seed, 'pilots': pilots, 'risk': risk, 'stability': stability}


class Handler(BaseHTTPRequestHandler):
    def respond(self, code, data, kind='application/json; charset=utf-8', filename=None):
        raw = json.dumps(clean(data), ensure_ascii=False, allow_nan=False).encode('utf-8') if kind.startswith('application/json') else data
        self.send_response(code)
        self.send_header('Content-Type', kind)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Content-Security-Policy', "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(raw)

    def trusted(self):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        host = self.headers.get('Host', '')
        origin = self.headers.get('Origin')
        return host in allowed and (not origin or origin in {'http://' + h for h in allowed})

    def do_GET(self):
        if not self.trusted():
            return self.respond(403, {'error': 'Local requests only'})
        path = urlparse(self.path).path
        try:
            if path == '/api/overview':
                return self.respond(200, overview())
            if path == '/api/status':
                with LOCK:
                    snapshot = dict(STATE)
                return self.respond(200, snapshot)
            if path in ('/api/export.csv', '/api/export.json'):
                with LOCK:
                    result = STATE['result']
                if result is None:
                    return self.respond(409, {'error': 'Алдымен жоспар құрыңыз'})
                seed = result['config']['seed']
                if path.endswith('.json'):
                    raw = json.dumps(result, ensure_ascii=False, indent=2).encode('utf-8')
                    return self.respond(200, raw, 'application/octet-stream', f'qadam-audit-seed-{seed}.json')
                cols = ['campaign_name','filter_arpu_segment','filter_data_segment','filter_call_segment',
                        'filter_current_tariff','target_tariff','channel']
                stream = io.StringIO(newline='')
                writer = csv.DictWriter(stream, fieldnames=cols)
                writer.writeheader(); writer.writerows(result['agent']['plan'])
                return self.respond(200, stream.getvalue().encode('utf-8-sig'), 'text/csv; charset=utf-8', f'campaign-plan-seed-{seed}.csv')
            assets = {'/': ('index.html', 'text/html; charset=utf-8'),
                      '/app.js': ('app.js', 'text/javascript; charset=utf-8'),
                      '/style.css': ('style.css', 'text/css; charset=utf-8'),
                      '/favicon.svg': ('favicon.svg', 'image/svg+xml')}
            if path not in assets:
                return self.respond(404, {'error': 'Not found'})
            name, kind = assets[path]
            return self.respond(200, (ROOT / 'web' / name).read_bytes(), kind)
        except (OSError, ValueError, KeyError) as exc:
            return self.respond(500, {'error': str(exc)})

    def do_POST(self):
        if not self.trusted():
            return self.respond(403, {'error': 'Local requests only'})
        if self.path != '/api/run':
            return self.respond(404, {'error': 'Not found'})
        try:
            length = int(self.headers.get('Content-Length', 0))
            if not 0 < length <= 2048:
                raise ValueError('Invalid request size')
            config = parse_config(json.loads(self.rfile.read(length)))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return self.respond(400, {'error': str(exc)})
        with LOCK:
            if STATE['running']:
                busy = True
            else:
                busy = False
                STATE.update(running=True, error=None, message='Талдау басталды')
        if busy:
            return self.respond(409, {'error': 'Алдыңғы есеп әлі орындалып жатыр'})
        threading.Thread(target=execute, args=(config,), daemon=True).start()
        return self.respond(202, {'accepted': True})


def main():
    parser = argparse.ArgumentParser(description='QADAM local campaign lab')
    parser.add_argument('--port', type=int, default=8765)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error('Use a port between 1024 and 65535')
    previous = RUNTIME / 'latest.json'
    if previous.exists():
        try:
            STATE['result'] = json.loads(previous.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            pass
    try:
        server = ThreadingHTTPServer(('127.0.0.1', args.port), Handler)
    except OSError as exc:
        parser.exit(1, f'Port {args.port} unavailable. Try --port {args.port + 1}. {exc}\n')
    print(f'QADAM ready: http://127.0.0.1:{args.port}  |  Ctrl+C to stop', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
