"""Один разбор событий прогона на всё дерево (правило 214, #141).

Вопрос «на каких событиях идёт прогон» разбирали четыре места своими
регулярками, и ответы расходились на однострочной форме и на `schedule:` вне
блока `on:`. Теперь разбор один — `scripts/workflow_on.py`, — а гейт ниже не
даёт завести пятый: регулярка, узнающая событие прогона, вне него — отказ.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

import workflow_on

КОРЕНЬ = Path(__file__).resolve().parents[1]
ПРЕДМЕТ = ("scripts", "src/claude_code_usage", ".claude/hooks")

#: Имена событий, по которым дерево что-то решает. Регулярка с любым из них —
#: второй разбор того же предмета.
УЗНАВАЕМЫЕ = ("pull_request", "schedule", "workflow_run", "workflow_dispatch")


@pytest.mark.parametrize(
    ("текст", "ожидание"),
    [
        ("on: push\n", {"push"}),
        ("on: [push, pull_request]\n", {"push", "pull_request"}),
        ('"on": [pull_request_target]\n', {"pull_request_target"}),
        (
            "on:\n  push:\n    branches: [main]\n  pull_request:\n",
            {"push", "pull_request"},
        ),
        ("'on':\n  - push\n  - schedule\n", {"push", "schedule"}),
        (
            "on:  # события\n  workflow_run:  # будильник\n    workflows: [x]\n",
            {"workflow_run"},
        ),
        ("name: x\njobs:\n  a:\n    runs-on: x\n", set()),
    ],
)
def test_формы_блока_on(текст: str, ожидание: set[str]) -> None:
    assert workflow_on.события(текст) == ожидание


def test_вложенный_ключ_не_событие() -> None:
    """`branches:` и `types:` под событием — не события."""
    текст = "on:\n  pull_request:\n    types: [opened]\n    branches: [main]\n"
    assert workflow_on.события(текст) == {"pull_request"}


def test_schedule_вне_on_не_расписание() -> None:
    """Прежняя перепись находила `schedule:` на любом отступе."""
    текст = "on:\n  push:\njobs:\n  a:\n    with:\n      schedule:\n"
    assert "schedule" not in workflow_on.события(текст)


def test_настоящие_прогоны_разбираются() -> None:
    каталог = КОРЕНЬ / ".github" / "workflows"
    события = {
        путь.name: workflow_on.события(путь.read_text(encoding="utf-8"))
        for путь in sorted(каталог.glob("*.yml"))
    }
    assert события, "прогонов нет — проверять нечего"
    assert all(события.values()), f"прогон без событий: {события}"


# ── гейт: событие узнаёт один разбор ─────────────────────────────────────

_RE = {"compile", "match", "search", "fullmatch", "findall", "finditer"}


def _регулярки_событий(путь: Path) -> list[int]:
    """Строки, где регулярка `re.*` узнаёт имя события прогона."""
    дерево = ast.parse(путь.read_text(encoding="utf-8"))
    return [
        узел.lineno
        for узел in ast.walk(дерево)
        if isinstance(узел, ast.Call)
        and isinstance(узел.func, ast.Attribute)
        and isinstance(узел.func.value, ast.Name)
        and узел.func.value.id == "re"
        and узел.func.attr in _RE
        and узел.args
        and isinstance(узел.args[0], ast.Constant)
        and isinstance(узел.args[0].value, str)
        and any(имя in узел.args[0].value for имя in УЗНАВАЕМЫЕ)
    ]


def test_событие_прогона_узнаёт_один_разбор() -> None:
    файлы = [
        путь for каталог in ПРЕДМЕТ for путь in sorted((КОРЕНЬ / каталог).glob("*.py"))
    ]
    assert len(файлы) >= 20, "у проверки должен быть предмет (075)"

    находки = [
        f"{путь.relative_to(КОРЕНЬ).as_posix()}:{строка}"
        for путь in файлы
        if путь.name != "workflow_on.py"
        for строка in _регулярки_событий(путь)
    ]
    assert not находки, (
        f"второй разбор событий прогона: {', '.join(находки)}. "
        "Спрашивайте workflow_on.события(), а не свою регулярку"
    )


def test_гейт_видит_регулярку_и_пропускает_прочее(tmp_path: Path) -> None:
    своя = tmp_path / "своя.py"
    своя.write_text(
        'import re\nre.search(r"^\\s+pull_request:", t)\n', encoding="utf-8"
    )
    чужая = tmp_path / "чужая.py"
    чужая.write_text('import re\nre.search(r"^\\s+group:", t)\n', encoding="utf-8")

    assert _регулярки_событий(своя) == [2]
    assert _регулярки_событий(чужая) == []
