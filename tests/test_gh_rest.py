"""Транспорт к площадке: у запроса назван срок, и транспорт один (#151).

Срок. `urlopen` без `timeout` ждёт ответа сколько угодно: зависший обмен держит
прогон до `timeout-minutes` джоба, а локально — вечно. CLAUDE.md требует
названного дедлайна у каждого вызова подпроцесса, но гейт дедлайнов видит
только `subprocess`, и сетевой вызов прошёл мимо. Аудит #139 нашёл это чтением.

Транспорт один. Срок назван в одном месте только пока к площадке ходит одно
место: второй `urlopen` в дереве завёл бы второй срок — или никакого. Приём —
тест проекта механизмов `test_no_mechanism_talks_to_the_platform_directly`
(правило 001, первая половина), переписанный на наш транспорт.
"""

import ast
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

import gh_rest

КОРЕНЬ = Path(__file__).resolve().parents[1]

#: Где живёт рабочий код, который мог бы позвать площадку мимо транспорта.
ПРЕДМЕТ = ("scripts", "src/claude_code_usage", ".claude/hooks")


class _Ответ:
    """Ответ площадки, достаточный для `with urlopen(...) as ответ`."""

    def __init__(self, тело: bytes = b"{}") -> None:
        self._тело = тело

    def __enter__(self) -> _Ответ:
        return self

    def __exit__(self, *_: object) -> None:
        return None

    def read(self) -> bytes:
        return self._тело


def test_у_запроса_назван_срок(monkeypatch: pytest.MonkeyPatch) -> None:
    """Каждый запрос уходит с названным сроком, а не с умолчанием «ждать вечно»."""
    переданное: dict[str, Any] = {}

    def urlopen(запрос: object, **kwargs: Any) -> _Ответ:
        переданное.update(kwargs)
        return _Ответ()

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    gh_rest.request("GET", "/repos/x/y")

    assert переданное.get("timeout") == gh_rest.REQUEST_TIMEOUT
    assert 0 < gh_rest.REQUEST_TIMEOUT <= 120


@pytest.mark.parametrize(
    "отказ",
    [TimeoutError("timed out"), urllib.error.URLError("timed out")],
    ids=["на чтении", "на соединении"],
)
def test_истёкший_срок_это_отказ_площадки(
    monkeypatch: pytest.MonkeyPatch, отказ: Exception
) -> None:
    """Истечение срока — `GitHubError`, а не необработанное падение.

    На соединении `urlopen` поднимает `URLError`, а на чтении — голый
    `TimeoutError`, мимо прежних `except`. Без перехвата срок превратил бы
    «площадка молчит» в трассировку вместо названного отказа.
    """

    def urlopen(запрос: object, **kwargs: Any) -> _Ответ:
        raise отказ

    monkeypatch.setattr(urllib.request, "urlopen", urlopen)
    with pytest.raises(gh_rest.GitHubError) as пойманное:
        gh_rest.request("GET", "/repos/x/y")

    assert пойманное.value.status == 0


def _импорты_urllib(путь: Path) -> list[int]:
    дерево = ast.parse(путь.read_text(encoding="utf-8"))
    строки = []
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.Import):
            if any(имя.name.split(".")[0] == "urllib" for имя in узел.names):
                строки.append(узел.lineno)
        elif isinstance(узел, ast.ImportFrom) and (узел.module or "").startswith(
            "urllib"
        ):
            строки.append(узел.lineno)
    return строки


def test_к_площадке_ходит_только_транспорт() -> None:
    """`urllib` вне `scripts/gh_rest.py` — второй транспорт со своим сроком."""
    файлы = [
        путь for каталог in ПРЕДМЕТ for путь in sorted((КОРЕНЬ / каталог).glob("*.py"))
    ]
    assert len(файлы) >= 20, "у проверки должен быть предмет (075)"

    мимо = [
        f"{путь.relative_to(КОРЕНЬ).as_posix()}:{строка}"
        for путь in файлы
        if путь.name != "gh_rest.py"
        for строка in _импорты_urllib(путь)
    ]
    assert not мимо, f"к площадке ходят мимо scripts/gh_rest.py: {', '.join(мимо)}"


def test_проверка_транспорта_видит_обход(tmp_path: Path) -> None:
    """Обе половины предиката на подделке: обход виден, чистый файл — нет."""
    обход = tmp_path / "обход.py"
    обход.write_text("from urllib.request import urlopen\n", encoding="utf-8")
    чистый = tmp_path / "чистый.py"
    чистый.write_text("import json\n", encoding="utf-8")

    assert _импорты_urllib(обход) == [1]
    assert _импорты_urllib(чистый) == []
