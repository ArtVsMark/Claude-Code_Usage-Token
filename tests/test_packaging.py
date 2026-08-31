"""Проверки самого каркаса.

Смысл этих тестов не в покрытии, а в том, чтобы гейт «тесты зелёные» перестал
проходить на пустоте (#17). Каждый из них падает на настоящей ошибке, которая
иначе обнаруживается поздно и не там, где сделана.
"""

from __future__ import annotations

import importlib
import re
import tomllib
from pathlib import Path
from typing import Any

import claude_code_usage

PYPROJECT = Path(__file__).resolve().parents[1] / "pyproject.toml"


def _pyproject() -> dict[str, Any]:
    with PYPROJECT.open("rb") as fh:
        data: dict[str, Any] = tomllib.load(fh)
    return data


def test_версия_объявлена_один_раз() -> None:
    """Версия живёт в одном месте, и это проверяется, а не подразумевается.

    Раньше она стояла в двух файлах и держалась тестом на равенство. Тест
    ловил расхождение, но не мешал ему появиться: при выпуске правили две
    строки, и вторую забывали. Теперь `pyproject.toml` объявляет версию
    динамической и берёт её из пакета — второй строки просто нет.
    """
    project = _pyproject()["project"]

    assert "version" not in project, (
        "версия вернулась в pyproject.toml — она снова в двух местах"
    )
    assert project["dynamic"] == ["version"]
    assert _pyproject()["tool"]["hatch"]["version"]["path"] == (
        "src/claude_code_usage/__init__.py"
    )
    # Литерала версии здесь нет намеренно. Он был — и оказался ТРЕТЬИМ местом,
    # где она живёт: правка версии роняла тест, который называется «версия
    # объявлена один раз». Проверяется форма, а совпадение с источником —
    # гейтом витрины, которому есть с чем сверять.
    assert re.fullmatch(r"\d+\.\d+\.\d+", claude_code_usage.__version__), (
        f"версия {claude_code_usage.__version__!r} не вида X.Y.Z — "
        "тег выпуска строго такой, и разойтись они не должны"
    )


def test_имя_дистрибутива_то_самое() -> None:
    """Имя дистрибутива после публикации не переигрывается.

    Решение принято в #19 и вынужденное: `claude-code-usage` и `claude-usage`
    заняты на PyPI посторонними пакетами. Тест держит его от случайного
    переименования при правке метаданных — цена ошибки здесь необратима.
    """
    assert _pyproject()["project"]["name"] == "claude-code-usage-meter"


def test_точка_входа_разрешается_в_вызываемое() -> None:
    """Объявленная точка входа обязана существовать.

    Опечатка в `[project.scripts]` не ломает ни установку, ни импорт пакета:
    она проявляется при первом запуске команды у чужого человека, и выглядит
    как поломка установки, а не как опечатка в одной строке.
    """
    scripts = _pyproject()["project"]["scripts"]
    assert list(scripts) == ["claude-code-usage-meter"]

    module_name, _, attr = scripts["claude-code-usage-meter"].partition(":")
    target = getattr(importlib.import_module(module_name), attr)
    assert callable(target)


def test_пакет_объявлен_типизированным() -> None:
    """`py.typed` обязан попадать в пакет, иначе строгость mypy не уедет к тем,
    кто пакет поставит: маркера нет — типы игнорируются молча."""
    assert (Path(claude_code_usage.__file__).parent / "py.typed").is_file()
