"""Списки площадки читаются целиком — или отказывают (правило 212, #141).

Обход страниц был, но мимо него шли три чтения: прогоны на голове PR в
обязательной проверке, проверки коммита в очереди и свежие прогоны общей
ветки. Первые два видели сотню записей и молчали об остальных — красная
проверка за сотней не попадала ни в вердикт, ни в мерж. Сам обход на
потолке страниц обрезал список молча.

Гейт держит то, что решается данными: литерал ``per_page`` вне транспорта
допустим только с **названным** пределом — именем константы, а не числом.
Число в вызове и есть «прочитали первую страницу и не сказали».
"""

from __future__ import annotations

import ast
from pathlib import Path
from typing import Any

import pytest

import gh_rest
import pr_check

КОРЕНЬ = Path(__file__).resolve().parents[1]
ПРЕДМЕТ = ("scripts", "src/claude_code_usage", ".claude/hooks")


def _площадка(
    monkeypatch: pytest.MonkeyPatch, страницы: list[Any]
) -> list[dict[str, Any]]:
    """Подменить запрос: отдавать страницы по очереди, запоминать параметры."""
    вызовы: list[dict[str, Any]] = []

    def request(method: str, path: str, **kwargs: Any) -> Any:
        вызовы.append(kwargs.get("params") or {})
        return страницы[len(вызовы) - 1] if len(вызовы) <= len(страницы) else []

    monkeypatch.setattr(gh_rest, "request", request)
    return вызовы


def _прогоны(n: int, всего: int) -> dict[str, Any]:
    return {"total_count": всего, "workflow_runs": [{"id": i} for i in range(n)]}


def test_обёрнутый_список_собирается_со_всех_страниц(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    вызовы = _площадка(monkeypatch, [_прогоны(100, 105), _прогоны(5, 105)])

    собрано = gh_rest.paged("/x", key="workflow_runs")

    assert len(собрано) == 105
    assert [в["page"] for в in вызовы] == [1, 2]


def test_недобор_против_total_count_это_отказ(monkeypatch: pytest.MonkeyPatch) -> None:
    """Площадка назвала больше, чем отдала: целым такой список не считается."""
    _площадка(monkeypatch, [_прогоны(3, 7)])

    with pytest.raises(gh_rest.TruncatedError, match="назвала 7"):
        gh_rest.paged("/x", key="workflow_runs")


def test_потолок_страниц_это_отказ_а_не_тишина(monkeypatch: pytest.MonkeyPatch) -> None:
    полная = [{"id": 0}] * gh_rest.PER_PAGE
    _площадка(monkeypatch, [полная] * gh_rest.MAX_PAGES)

    with pytest.raises(gh_rest.TruncatedError, match="прочитан не целиком"):
        gh_rest.paged("/x")


def test_обрезка_читается_как_отказ_площадки() -> None:
    """Вызывающие ловят отказ площадки — и обрезку ловят тем же."""
    assert issubclass(gh_rest.TruncatedError, gh_rest.GitHubError)


def test_голый_список_как_прежде(monkeypatch: pytest.MonkeyPatch) -> None:
    _площадка(monkeypatch, [[{"n": 1}, {"n": 2}]])

    assert gh_rest.paged("/x") == [{"n": 1}, {"n": 2}]


def test_обязательная_проверка_читает_прогоны_за_сотней(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Прогон на второй странице — именно тот, что прежде не читался."""
    вторая = {
        "total_count": 101,
        "workflow_runs": [{"id": 100, "conclusion": "failure"}],
    }
    _площадка(monkeypatch, [_прогоны(100, 101), вторая])

    прогоны = pr_check.fetch_runs("o/r", "sha")

    assert len(прогоны) == 101
    assert прогоны[-1]["conclusion"] == "failure"


# ── гейт: предел страницы назван вслух ───────────────────────────────────


def _безымянные_пределы(путь: Path) -> list[int]:
    """Строки, где ``"per_page"`` получает число, а не имя предела."""
    дерево = ast.parse(путь.read_text(encoding="utf-8"))
    return [
        значение.lineno
        for узел in ast.walk(дерево)
        if isinstance(узел, ast.Dict)
        for ключ, значение in zip(узел.keys, узел.values, strict=True)
        if isinstance(ключ, ast.Constant)
        and ключ.value == "per_page"
        and isinstance(значение, ast.Constant)
    ]


def test_предел_страницы_вне_транспорта_назван() -> None:
    файлы = [
        путь for каталог in ПРЕДМЕТ for путь in sorted((КОРЕНЬ / каталог).glob("*.py"))
    ]
    assert len(файлы) >= 20, "у проверки должен быть предмет (075)"

    находки = [
        f"{путь.relative_to(КОРЕНЬ).as_posix()}:{строка}"
        for путь in файлы
        if путь.name != "gh_rest.py"
        for строка in _безымянные_пределы(путь)
    ]
    assert not находки, (
        f"per_page числом — первая страница вместо списка: {', '.join(находки)}. "
        "Читайте gh_rest.paged(..., key=...) или назовите предел константой"
    )


def test_гейт_пределов_видит_число_и_пропускает_имя(tmp_path: Path) -> None:
    число = tmp_path / "число.py"
    число.write_text('p = {"per_page": 100}\n', encoding="utf-8")
    имя = tmp_path / "имя.py"
    имя.write_text('ПРЕДЕЛ = 20\np = {"per_page": ПРЕДЕЛ}\n', encoding="utf-8")

    assert _безымянные_пределы(число) == [1]
    assert _безымянные_пределы(имя) == []
