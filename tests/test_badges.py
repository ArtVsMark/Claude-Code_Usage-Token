"""Проверки сборки значков (#160 каталога).

Сборщик ничего не считает сам: значение каждого значка даёт то же
`preflight.expected_badge`, которым гейт витрины сверял значок, пока тот лежал
в `main`. Поэтому здесь проверяется не арифметика, а границы — что собирается,
что пропускается и на чём сборка обязана отказать.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import badges
import preflight

КОРЕНЬ = Path(__file__).resolve().parents[1]


def _витрина(каталог: Path, вопросы: list[dict[str, Any]]) -> Path:
    путь = каталог / preflight.SHOWCASE_SET
    путь.parent.mkdir(parents=True, exist_ok=True)
    путь.write_text(
        json.dumps({"schema": "1.0", "questions": вопросы}, ensure_ascii=False),
        encoding="utf-8",
    )
    return каталог


def _источник_версии(каталог: Path, версия: str = "4.5.6") -> None:
    (каталог / "pyproject.toml").write_text(
        '[project]\ndynamic = ["version"]\n\n'
        '[tool.hatch.version]\npath = "src/pkg/__init__.py"\n',
        encoding="utf-8",
    )
    (каталог / "src" / "pkg").mkdir(parents=True, exist_ok=True)
    (каталог / "src" / "pkg" / "__init__.py").write_text(
        f'__version__ = "{версия}"\n', encoding="utf-8"
    )


ЗНАЧОК: dict[str, Any] = {
    "id": "version",
    "ask": "какая версия у текущей головы",
    "badge": ".github/badges/version.json",
    "branch": "badges",
}

ПРОБЕЛ: dict[str, Any] = {
    "id": "pypi",
    "ask": "какая версия опубликована в PyPI",
    "absent": "предмета нет: дистрибутив не публиковался ни разу",
}


def test_собирает_объявленный_значок(tmp_path: Path) -> None:
    _витрина(tmp_path, [ПРОБЕЛ, ЗНАЧОК])
    _источник_версии(tmp_path, "4.5.6")

    файлы = badges.build(tmp_path)

    assert [п.relative_to(tmp_path).as_posix() for п in файлы] == [
        ".github/badges/version.json"
    ]
    лежит = json.loads(файлы[0].read_text(encoding="utf-8"))
    assert лежит["message"] == "4.5.6"


def test_значение_то_же_что_у_гейта(tmp_path: Path) -> None:
    """Сборщик и гейт обязаны брать значение из ОДНОГО правила (022)."""
    _витрина(tmp_path, [ЗНАЧОК])
    _источник_версии(tmp_path, "9.9.9")

    (путь,) = badges.build(tmp_path)

    assert json.loads(путь.read_text(encoding="utf-8")) == preflight.expected_badge(
        "version", tmp_path
    )


def test_вопрос_без_значка_пропускается(tmp_path: Path) -> None:
    _витрина(tmp_path, [ПРОБЕЛ])
    assert badges.build(tmp_path) == []


def test_значок_без_правила_вывода_это_отказ(tmp_path: Path) -> None:
    """Пустой файл на ветке отвечал бы вместо живого числа."""
    _витрина(
        tmp_path,
        [{"id": "coverage", "ask": "какая доля покрыта", "badge": ".github/b/c.json"}],
    )

    with pytest.raises(ValueError, match="правила вывода"):
        badges.build(tmp_path)


def test_можно_собрать_в_другой_каталог(tmp_path: Path) -> None:
    """Прогон копит собранное отдельно от дерева — там же и проверяется."""
    _витрина(tmp_path, [ЗНАЧОК])
    _источник_версии(tmp_path)
    куда = tmp_path / "выход"

    (путь,) = badges.build(tmp_path, out=куда)

    assert путь == куда / ".github/badges/version.json"
    assert not (tmp_path / ".github/badges/version.json").exists()


def test_отказ_сборки_отдельным_кодом(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """«Собрать не вышло» и «нечего собирать» обязаны различаться кодом."""
    _витрина(tmp_path, [ЗНАЧОК])  # источника версии нет

    код = badges.main([str(tmp_path)])

    assert код == badges.EXIT_BROKEN
    assert "::error::" in capsys.readouterr().err


def test_набор_проекта_собирается(tmp_path: Path) -> None:
    """Сборщик на живом дереве проекта: значок обязан получиться."""
    файлы = badges.build(КОРЕНЬ, out=tmp_path)

    assert файлы, "витрина проекта не объявила ни одного значка"
    for путь in файлы:
        assert json.loads(путь.read_text(encoding="utf-8"))["schemaVersion"] == 1
