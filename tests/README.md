# Область применения проверок

`test_agent.py` содержит общие проверки корневого `agent.py`: контракт, лимиты,
воспроизводимость, зависимость решений от наблюдений пилотов и обработку ошибок.
`test_split_eval.py` проверяет внешнюю диагностику. `test_demo.py` проверяет демо.

Все шесть тестов класса `Stage1ExperimentalExplorationTests` в
`test_exploration.py` относятся **только** к стратегии
`experiments/stage1_expanded_search/agent.py`. Перед загрузкой проверяется SHA-256
`43b88055a4f71af19b39ff49694b124b11873cd812ff9a0b9388e57867279658`.
Загрузка через `split_eval.load_agent` не пишет кеш рядом с архивным исходником.
Контролируемые наблюдения этих тестов не используют модель эффектов организаторов.

Восстановленная основная стратегия не проходит требование продолжать поиск
после первых шести отрицательных пилотов. Отдельный класс `MainKnownLimitTests`
честно фиксирует это ограничение: выполнены шесть пилотов, `x6`/`x7` не проверены,
осталось 14 пилотов, 14400 контактов и весь бюджет. Зелёный результат этой
характеризационной проверки не означает исправления ограничения.

Команды из корня проекта:

```powershell
.\.venv\Scripts\python.exe -m unittest tests.test_agent tests.test_split_eval tests.test_demo -v
.\.venv\Scripts\python.exe -m unittest tests.test_exploration.Stage1ExperimentalExplorationTests -v
.\.venv\Scripts\python.exe -m unittest tests.test_exploration.MainKnownLimitTests -v
```

Полный запуск включает обе явно названные области, не переносит результаты
эксперимента на основного агента и не меняет сохранённые версии:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```
