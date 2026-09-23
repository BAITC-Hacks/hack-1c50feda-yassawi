"""Optional Russian demo; the submission agent has no Streamlit dependency."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import sys

import pandas as pd
import streamlit as st

from agent import Agent
from local_eval import evaluate_agent

ROOT = Path(__file__).parent


class DemoAgent(Agent):
    def act(self, env):
        plan = super().act(env)
        self.snapshot = {"campaigns": plan, "pilots": list(env.pilot_history), "log": self.log,
                         "budget_after_pilots": env.remaining_budget,
                         "contacts_after_pilots": env.remaining_contacts}
        return plan


def run_demo(seed):
    agent = DemoAgent()
    output = io.StringIO()
    with contextlib.redirect_stdout(output):
        result = evaluate_agent(agent, seed=int(seed), verbose=False)
    if not result or "Агент упал" in output.getvalue() or "отброшена" in output.getvalue():
        raise RuntimeError(output.getvalue() or "Оценка не получена")
    return {**agent.snapshot, "evaluation": result, "seed": int(seed)}


st.set_page_config(page_title="BeeGrowth Agent", page_icon="🐝", layout="wide")
st.title("🐝 BeeGrowth Agent")
st.warning("Все данные синтетические. Локальная мок-оценка не гарантирует результат скрытого судейства.")
profile = pd.read_csv(ROOT / "customer_profile.csv")
left, right = st.columns(2)
left.metric("Абонентов в исходной аудитории", f"{len(profile):,}")
right.metric("Исходная сумма predicted_arpu — не доход агента", f"{profile.predicted_arpu.sum():,.0f}")
st.dataframe(profile.groupby(["current_tariff", "arpu_segment"], observed=True).agg(
    Абонентов=("ID_NUMBER", "size"), Исходный_ARPU=("predicted_arpu", "sum")).reset_index(), hide_index=True)
seed = st.number_input("Seed новой независимой мок-среды", min_value=0, max_value=2147483647, value=42)
if st.button("Запустить агента", type="primary"):
    try:
        with st.spinner("Проверяем гипотезы пилотами и формируем план…"):
            st.session_state["report"] = run_demo(seed)
    except (RuntimeError, ValueError, OSError) as exc:
        st.error(str(exc))

if "report" in st.session_state:
    report = st.session_state["report"]
    st.caption(f"Сохранённый запуск: seed={report['seed']}. Изменение элементов страницы не запускает пилоты.")
    forecasts, observations, evaluation, explanation = st.tabs([
        "Прогноз агента", "Наблюдения пилотов", "Локальный оценщик", "Объяснения"])
    with forecasts:
        st.dataframe(pd.DataFrame(report["campaigns"]), hide_index=True)
        selected = [e for e in report["log"] if e["event"] == "selected"]
        st.dataframe(pd.DataFrame([{ "Кампания": e["campaign"]["campaign_name"],
                                    "Аудитория": e["size"], "Средний прогноз net": e["mean_net"],
                                    "Прогноз с поправкой на риск": e["risk_adjusted_net"]} for e in selected]), hide_index=True)
        st.caption("Прогноз относится к финальным кампаниям. Эффекты пилотов к нему не прибавлены: возможны повторные контакты.")
    with observations:
        st.dataframe(pd.DataFrame(report["pilots"]), hide_index=True)
        st.caption("observed_lift_ratio — шумное наблюдение уже с учётом выбранного канала.")
        st.write(f"После пилотов: бюджет {report['budget_after_pilots']:,.0f}; контактов доступно {report['contacts_after_pilots']:,}.")
    with evaluation:
        result = report["evaluation"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Чистый дополнительный результат", f"{result['net_arpu_gain']:,.0f}")
        c2.metric("Все расходы, включая пилоты", f"{result['total_cost']:,.0f}")
        c3.metric("Все контакты, включая повторные", f"{result['total_contacts']:,}")
        st.write(f"Осталось после всего плана: {100000-result['total_cost']:,.0f} у.е.; {15000-result['total_contacts']:,} контактов.")
        st.write(f"Уникальных абонентов: {result['unique_customers_targeted']:,}. Статус оценщика: {result['status']}.")
        st.caption("Официальный локальный оценщик применяет дедупликацию эффекта. Его модель не передаётся агенту.")
    with explanation:
        reasons = {"candidate": "Гипотезы из сглаженной истории", "pilot": "Проверки и обновление оценок",
                   "selected": "Выбранные кампании", "rejected": "Отказы", "plan": "Остатки ресурсов"}
        category = st.selectbox("Этап решения", list(reasons), format_func=reasons.get)
        st.json([e for e in report["log"] if e["event"] == category])
    st.download_button("Скачать отчёт запуска JSON", json.dumps(report, ensure_ascii=False, indent=2),
                       file_name="beegrowth_report.json", mime="application/json")

st.divider()
st.caption("Официальный submission всегда создаётся make_submission.py с seed=42 и может отличаться от выбранного демо-запуска.")
if st.button("Создать официальный submission.csv"):
    try:
        process = subprocess.run([sys.executable, "make_submission.py"], cwd=ROOT, capture_output=True,
                                 timeout=600, check=True)
        st.session_state["submission"] = (ROOT / "submission.csv").read_bytes()
    except (OSError, subprocess.SubprocessError) as exc:
        st.error(f"Не удалось создать submission: {exc}")
if "submission" in st.session_state:
    st.download_button("Скачать официальный submission.csv", st.session_state["submission"],
                       file_name="submission.csv", mime="text/csv")
