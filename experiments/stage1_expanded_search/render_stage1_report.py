"""Render a Russian Markdown report from the two completed stage-one comparisons.

This script only reads completed comparison JSON files. It never runs an agent,
changes a snapshot, or produces a CSV. Formatting is deterministic.
"""
import argparse
import json
from pathlib import Path
import statistics


ROOT = Path(__file__).resolve().parent
DIAGNOSTICS = ROOT / "diagnostics" / "stage1"
METRICS = (
    ("Общий net: медиана [мин.; макс.]", "net_combined"),
    ("Добавочный net финала: медиана [мин.; макс.]", "final_incremental_net"),
)


def number(value, digits=2):
    if value is None:
        return "нет данных"
    return f"{value:,.{digits}f}".replace(",", " ")


def spread(values, digits=2):
    return f"{number(values['median'], digits)} [{number(values['min'], digits)}; {number(values['max'], digits)}]"


def rate(values, n):
    return f"{values['count']}/{n} ({100 * values['fraction']:.1f}%)"


def forecast_median(runs, key):
    values = [r[key] for r in runs if r[key] is not None]
    if len(values) != len(runs):
        return "нет полного прогноза"
    return number(statistics.median(values))


def financial_changes(group):
    before = group["before"]["summary"]
    after = group["after"]["summary"]
    return [after["net_combined"]["median"] - before["net_combined"]["median"],
            after["final_incremental_net"]["median"] - before["final_incremental_net"]["median"],
            after["positive_combined"]["fraction"] - before["positive_combined"]["fraction"],
            before["negative_final_incremental"]["fraction"] - after["negative_final_incremental"]["fraction"]]


def cohort_outcome(group):
    before = group["before"]["summary"]
    after = group["after"]["summary"]
    changes = financial_changes(group)
    if any(value < 0 for value in changes):
        verdict = ("Результат смешанный: часть финансовых показателей ухудшилась."
                   if any(value > 0 for value in changes) else "Финансовые показатели ухудшились.")
    elif any(value > 0 for value in changes):
        verdict = "На этой мок-выборке финансовые показатели выросли или остались прежними."
    else:
        verdict = "Перечисленные финансовые показатели не изменились."
    n = before["n_runs"]
    return (f"{verdict} Медиана общего net: {number(before['net_combined']['median'])} → "
            f"{number(after['net_combined']['median'])}; медиана добавочного net финала: "
            f"{number(before['final_incremental_net']['median'])} → {number(after['final_incremental_net']['median'])}. "
            f"Положительный общий результат: {before['positive_combined']['count']}/{n} → "
            f"{after['positive_combined']['count']}/{n}; отрицательный добавочный вклад финала: "
            f"{before['negative_final_incremental']['count']}/{n} → {after['negative_final_incremental']['count']}/{n}.")


def _table(group):
    versions = [group[version] for version in ("before", "after")]
    summaries = [version["summary"] for version in versions]
    lines = ["| Показатель | До | После |", "|---|---:|---:|"]

    def add(label, cells):
        lines.append(f"| {label} | {' | '.join(cells)} |")

    for label, key in METRICS:
        add(label, [spread(s[key]) for s in summaries])
    add("Только пилоты: медиана net", [number(s["net_pilots_only"]["median"]) for s in summaries])
    add("Финал отдельно: медиана net", [number(s["net_final_standalone"]["median"]) for s in summaries])
    add("Прогноз полного финала: медиана mean net",
        [forecast_median(v["runs"], "forecast_final_mean_net") for v in versions])
    add("Прогноз полного финала: медиана с поправкой на риск",
        [forecast_median(v["runs"], "forecast_final_risk_adjusted_net") for v in versions])
    add("Положительный общий net", [rate(s["positive_combined"], s["n_runs"]) for s in summaries])
    add("Отрицательный добавочный net финала",
        [rate(s["negative_final_incremental"], s["n_runs"]) for s in summaries])
    for label, key in (("Расходы: медиана / максимум", "total_cost"),
                       ("Контакты: медиана / максимум", "total_contacts")):
        add(label, [f"{number(s[key]['median'])} / {number(s[key]['max'])}" for s in summaries])
    for label, key in (("Пилоты: медиана [мин.; макс.]", "n_pilots"),
                       ("Гипотезы с учётом канала: медиана [мин.; макс.]", "unique_hypotheses_with_channel"),
                       ("Гипотезы без учёта канала: медиана [мин.; макс.]", "unique_hypotheses_without_channel"),
                       ("Финальные кампании: медиана [мин.; макс.]", "n_final_campaigns")):
        add(label, [spread(s[key], 1) for s in summaries])
    add("Все ограничения соблюдены", [rate(s["limits_ok"], s["n_runs"]) for s in summaries])
    add("Отброшено финальных кампаний санитарной проверкой",
        [str(sum(r["validation"]["sanitizer_dropped"] for r in v["runs"])) for v in versions])
    add("Отсечено из-за числа финальных кампаний",
        [str(sum(r["validation"]["campaign_count_truncated"] for r in v["runs"])) for v in versions])
    add("Кампаний обрезано оценщиком",
        [str(sum(r["validation"]["scorer_trimmed"] for r in v["runs"])) for v in versions])
    for label, key in (("Время act: медиана, с", "act_seconds"),
                       ("Время оценки: медиана, с", "evaluation_seconds"),
                       ("Весь запуск: медиана, с", "total_seconds")):
        add(label, [number(s[key]["median"], 3) for s in summaries])
    add("Суммарное время запусков версии, с", [number(s["sum_total_seconds"], 3) for s in summaries])
    return lines


def _load_comparison(path, cohort, seeds):
    document = json.loads(path.read_text(encoding="utf-8"))
    group = document["cohorts"][cohort]
    if document["predeclared_cohorts"][cohort] != seeds:
        raise ValueError(f"{path.name}: неверный заранее заданный диапазон {cohort}")
    if document["snapshot_before"] != document["snapshot_after"]:
        raise ValueError(f"{path.name}: проверка снимка до и после не совпадает")
    for version in ("before", "after"):
        recorded = [row["seed"] for row in group[version]["runs"]]
        if recorded != seeds or group[version]["summary"]["n_runs"] != len(seeds):
            raise ValueError(f"{path.name}: неполная или непарная выборка {version}")
    return document, group


def render(primary_path=DIAGNOSTICS / "comparison_primary.json",
           additional_path=DIAGNOSTICS / "comparison_additional.json"):
    primary, first = _load_comparison(Path(primary_path), "primary", list(range(100)))
    additional, second = _load_comparison(Path(additional_path), "additional", list(range(100, 150)))
    if primary["source_sha256"] != additional["source_sha256"]:
        raise ValueError("Хеши версий различаются между основной и дополнительной проверками")
    if primary["snapshot_after"] != additional["snapshot_after"]:
        raise ValueError("Снимок различается между двумя проверками")
    has_decline = any(change < 0 for group in (first, second) for change in financial_changes(group))
    financial_verdict = (
        "Поведенческое исправление не подтверждено как общее финансовое улучшение: "
        "в сравнении есть ухудшившиеся показатели. Текущую версию нельзя считать более прибыльной заменой на основании этих результатов. "
        if has_decline else
        "На этих мок-выборках нет ухудшения медиан общего и добавочного net, доли положительных общих результатов и доли отрицательного добавочного net. "
        "Этого недостаточно для вывода о прибыли в скрытой среде. ")
    lines = ["# BeeGrowth: сравнение первого этапа", "",
             "Изменение устраняет жёсткую остановку поиска новых гипотез после первых шести пилотов. "
             + financial_verdict +
             "Сохранённый агент и его диагностика остаются доступны для сравнения и возможного возврата к прежней версии.", "",
             "Отчёт построен из завершённых парных прогонов. В каждой новой среде агент выполняется один раз. "
             "После завершения `Agent.act()` официальный оценщик отдельно рассчитывает пилоты, те же пилоты с финалом и финал без пилотов. "
             "Модель оценки, конкретные ID пилотов и оценки не передаются агенту.", "",
             "`final_incremental_net = net_combined - net_pilots_only`. Отдельную оценку финала нельзя складывать с оценкой пилотов: "
             "при пересечениях оценщик учитывает лучший эффект для каждого абонента, но все расходы на контакты. "
             "Прогнозы агента относятся к полным финальным аудиториям до поправки на пересечение с пилотами; "
             "поправка на риск `mean - std` не гарантирует прибыль.", ""]
    lines.extend(["Методика и формула оценки ценности эксперимента приведены в [method.md](method.md); "
                  "предварительная фиксация диапазонов и версий — в [experiment_plan.json](experiment_plan.json). "
                  "В таблицах время `act` включает только один вызов агента; время оценки начинается после его возврата. "
                  "Весь запуск дополнительно включает создание среды и внешние диагностические расчёты.", ""])
    for title, document, group in (("Seed 0–99: основное сравнение", primary, first),
                                    ("Seed 100–149: дополнительная проверка", additional, second)):
        lines.extend([f"## {title}", "", cohort_outcome(group), ""])
        lines.extend(_table(group))
        paired = group["paired_difference_after_minus_before"]
        n = group["before"]["summary"]["n_runs"]
        better, worse = paired["combined_better_count"], paired["combined_worse_count"]
        lines.extend(["", "Парные разницы «после − до» на одинаковых seed:", "",
                      f"- Общий net: медиана [мин.; макс.] = {spread(paired['net_combined'])}.",
                      f"- Добавочный net финала: медиана [мин.; макс.] = {spread(paired['final_incremental_net'])}.",
                      f"- Общий net выше в {better}/{n}, ниже в {worse}/{n}, равен в {n - better - worse}/{n} парах.",
                      f"- Время всего парного сравнения: {number(document['wall_seconds'], 3)} с.", ""])
    baseline42 = {version: next(row for row in first[version]["runs"] if row["seed"] == 42)
                  for version in ("before", "after")}
    lines.extend(["## Контроль воспроизводимости seed 42", "",
                  "Seed 42 служит проверкой воспроизводимости сохранённой версии, а не целевым результатом новой политики.", "",
                  "| Показатель | До | После |", "|---|---:|---:|"])
    for label, key in (("Только пилоты", "net_pilots_only"), ("Пилоты + финал", "net_combined"),
                       ("Добавочный вклад финала", "final_incremental_net"),
                       ("Финал отдельно", "net_final_standalone"),
                       ("Прогноз полного финала: mean net", "forecast_final_mean_net"),
                       ("Прогноз полного финала: с поправкой на риск", "forecast_final_risk_adjusted_net")):
        values = [number(baseline42[v][key], 6) for v in ("before", "after")]
        lines.append(f"| {label} | {' | '.join(values)} |")
    lines.extend(["", "## Границы сравнения", "",
                  "Диапазоны 0–99 и 100–149 заданы до измерений. Обе проверки используют одинаковые хеши сохранённой и изменённой версий; "
                  "параметры между ними не менялись. Результаты дополнительной проверки не используются для подбора параметров. "
                  "Смена seed меняет выборку пилотов и шум наблюдений в одной фиксированной мок-модели; это не проверка на 150 разных моделях поведения.", "",
                  "Метрика ценности нового пилота — приближение на основе неопределённости, ожидаемого результата и расхода ресурсов, "
                  "а не точное решение задачи ценности информации. Прогнозы могут существенно расходиться с мок-оценкой. "
                  "Финальный план выбирается жадно; прогнозы пока не корректируются на пересечения с уже проведёнными пилотами.", "",
                  "В первом этапе модель каналов и историческое ограничение top-3 целевых тарифов на сегмент сохранены. "
                  "Расширение кандидатов вне top-3 и пересмотр модели каналов требуют отдельных изменений и проверок. "
                  "Время измерено по настенным часам и зависит от нагрузки машины. Положительный общий результат не означает положительный добавочный вклад каждого финального плана.", "",
                  "## Исходные артефакты", "",
                  "- [Основное сравнение](comparison_primary.json).",
                  "- [Дополнительная проверка](comparison_additional.json).",
                  "- [Тесты, официальные команды и целостность файлов](checks.md).",
                  "- [Сохранённый агент](../../snapshots/before_stage1/agent.py).",
                  "- [Манифест снимка](../../snapshots/before_stage1/manifest.json).", "",
                  f"SHA-256 до: `{primary['source_sha256']['before']}`.", "",
                  f"SHA-256 после: `{primary['source_sha256']['after']}`.", "",
                  f"Проверены {primary['snapshot_after']['files_verified']} файлов снимка; "
                  f"SHA-256 манифеста: `{primary['snapshot_after']['manifest_sha256']}`.", ""])
    lines.extend(["## Команды воспроизведения", "",
                  "Из корня проекта, после создания снимка:", "", "```powershell",
                  r".\.venv\Scripts\python.exe split_eval.py --cohort primary --output diagnostics/stage1/comparison_primary.json",
                  r".\.venv\Scripts\python.exe split_eval.py --cohort additional --output diagnostics/stage1/comparison_additional.json",
                  r".\.venv\Scripts\python.exe render_stage1_report.py", "```", "",
                  "Оценочный инструмент создаёт JSON только при отсутствии файла и отказывается перезаписывать существующий. "
                  "Для повторного запуска задайте новые имена через `--output` внутри `diagnostics/stage1`, затем передайте их "
                  "генератору отчёта через `--primary` и `--additional`. Снимок и CSV при этом не изменяются.", ""])
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--primary", type=Path, default=DIAGNOSTICS / "comparison_primary.json")
    parser.add_argument("--additional", type=Path, default=DIAGNOSTICS / "comparison_additional.json")
    parser.add_argument("--output", type=Path, default=DIAGNOSTICS / "report.md")
    args = parser.parse_args()
    output = args.output.resolve()
    if not output.is_relative_to(DIAGNOSTICS) or output.suffix != ".md":
        parser.error("Output must be a .md file inside diagnostics/stage1")
    try:
        content = render(args.primary, args.additional)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(f"Нужны обе завершённые совместимые проверки: {exc}")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8", newline="\n")
    print(f"Saved {output}")


if __name__ == "__main__":
    main()
