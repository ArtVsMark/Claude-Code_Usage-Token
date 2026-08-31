"""Проверки записей changelog (#10).

Ложное «прошло» здесь означает выпуск с недостоверным описанием возможностей —
то, что 📦 Релиз-инженер имеет право остановить. Ложное «не прошло» заворачивает
верную запись, и первой же правкой гейт выключают.

Поэтому на каждую находку — по два теста.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import changelog


def _фрагмент(каталог: Path, имя: str, текст: str) -> Path:
    каталог.mkdir(parents=True, exist_ok=True)
    путь = каталог / имя
    путь.write_text(текст, encoding="utf-8")
    return путь


def _дерево(tmp_path: Path, файлы: dict[str, str]) -> Path:
    каталог = tmp_path / changelog.FRAGMENTS
    for имя, текст in файлы.items():
        _фрагмент(каталог, имя, текст)
    return tmp_path


# ── формат имени ──────────────────────────────────────────────────────────


def test_запись_по_формату_разбирается(tmp_path: Path) -> None:
    путь = _фрагмент(tmp_path, "10.added.md", "Записи едут фрагментами.\n")
    разобрано = changelog.parse(путь)
    assert isinstance(разобрано, changelog.Fragment)
    assert (разобрано.issue, разобрано.kind) == (10, "added")


def test_имя_не_по_формату_находится(tmp_path: Path) -> None:
    путь = _фрагмент(tmp_path, "запись.md", "Текст.\n")
    assert isinstance(changelog.parse(путь), str)


def test_неизвестный_вид_находится(tmp_path: Path) -> None:
    """Список видов закрытый: неизвестный раздел потерялся бы при сборке молча."""
    путь = _фрагмент(tmp_path, "10.improved.md", "Текст.\n")
    претензия = changelog.parse(путь)
    assert isinstance(претензия, str)
    assert "неизвестен" in претензия


def test_пустая_запись_находится(tmp_path: Path) -> None:
    """Пустой файл — то же, что отсутствие записи, но выглядит как наличие."""
    путь = _фрагмент(tmp_path, "10.added.md", "   \n\n")
    претензия = changelog.parse(путь)
    assert isinstance(претензия, str)
    assert "пустая запись" in претензия


def test_readme_не_считается_записью(tmp_path: Path) -> None:
    """Описание формата лежит рядом с записями и записью не является."""
    корень = _дерево(
        tmp_path, {"README.md": "# Формат\n", "10.added.md": "Русская запись.\n"}
    )
    фрагменты, претензии = changelog.collect(корень)
    assert len(фрагменты) == 1
    assert претензии == []


def test_каталога_нет_собирать_не_из_чего(tmp_path: Path) -> None:
    """Проверка, не нашедшая предмета, обязана сказать об этом."""
    _, претензии = changelog.collect(tmp_path)
    assert len(претензии) == 1
    assert "каталога нет" in претензии[0]


# ── язык записей ──────────────────────────────────────────────────────────


def test_русская_запись_проходит(tmp_path: Path) -> None:
    корень = _дерево(tmp_path, {"10.added.md": "Записи едут фрагментами.\n"})
    фрагменты, _ = changelog.collect(корень)
    assert changelog.language_warnings(фрагменты) == []


def test_непереведённая_запись_находится(tmp_path: Path) -> None:
    корень = _дерево(tmp_path, {"10.added.md": "Entries now ship as fragments.\n"})
    фрагменты, _ = changelog.collect(корень)
    замечания = changelog.language_warnings(фрагменты)
    assert len(замечания) == 1
    assert "нет ни одной русской буквы" in замечания[0]


def test_запись_из_идентификаторов_законна(tmp_path: Path) -> None:
    """Ложное «не прошло», которое напрашивается само.

    «`ruff` поднят до 0.6» не содержит ни одной русской буквы по природе.
    Эвристика «нет кириллицы — не переведено» завернула бы верную запись, и
    гейт выключили бы вместе со всем, что он ловит. Поэтому исключение
    объявляется строкой-маркером, а не угадывается.
    """
    корень = _дерево(
        tmp_path,
        {"10.changed.md": f"{changelog.IDENTIFIERS_ONLY}\n`ruff` 0.6 → 0.16\n"},
    )
    фрагменты, _ = changelog.collect(корень)
    assert changelog.language_warnings(фрагменты) == []


def test_маркер_не_прячет_текст_целиком(tmp_path: Path) -> None:
    """Маркер снимает требование к языку, а не к содержанию."""
    корень = _дерево(
        tmp_path, {"10.changed.md": f"{changelog.IDENTIFIERS_ONLY}\n`ruff` 0.16\n"}
    )
    фрагменты, _ = changelog.collect(корень)
    assert фрагменты[0].body == "`ruff` 0.16"


# ── кому запись нужна ─────────────────────────────────────────────────────


def test_поведение_требует_записи() -> None:
    assert changelog.requires_entry(["area/ci", "enhancement"])
    assert changelog.requires_entry(["area/core", "bug"])


def test_правка_документов_записи_не_требует() -> None:
    """Требовать запись от правки документа значит разводить шум.

    В шуме теряется настоящая запись — та, ради которой гейт и заведён.
    """
    assert not changelog.requires_entry(["area/docs", "documentation"])


# ── сборка ────────────────────────────────────────────────────────────────


def test_сборка_разносит_по_разделам(tmp_path: Path) -> None:
    корень = _дерево(
        tmp_path,
        {
            "10.added.md": "Записи фрагментами.\n",
            "8.fixed.md": "Очередь не падает на конфликте.\n",
            "2.added.md": "Команда снятия замера.\n",
        },
    )
    фрагменты, _ = changelog.collect(корень)

    собрано = changelog.render(фрагменты, "0.1.0")

    assert "## 0.1.0" in собрано
    assert "### Добавлено" in собрано
    assert "### Исправлено" in собрано
    # Порядок по номеру задачи, а не по имени файла: строкой #10 встало бы
    # раньше #2.
    assert собрано.index("(#2)") < собрано.index("(#10)")


def test_пустой_раздел_не_печатается(tmp_path: Path) -> None:
    корень = _дерево(tmp_path, {"10.added.md": "Записи фрагментами.\n"})
    фрагменты, _ = changelog.collect(корень)

    собрано = changelog.render(фрагменты, "0.1.0")

    assert "### Убрано" not in собрано


def test_многострочная_запись_склеивается(tmp_path: Path) -> None:
    """В документе запись — один пункт списка, как бы её ни разбили в файле."""
    корень = _дерево(tmp_path, {"10.added.md": "Первая строка\nи вторая.\n"})
    фрагменты, _ = changelog.collect(корень)
    assert "- Первая строка и вторая. (#10)" in changelog.render(фрагменты, "0.1.0")


# ── строгость по месту ────────────────────────────────────────────────────


def test_на_pr_язык_замечание(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Запись ещё правится: красное здесь приучало бы читать красное как фон."""
    monkeypatch.setattr(
        changelog, "ROOT", _дерево(tmp_path, {"10.added.md": "In English.\n"})
    )

    assert changelog.main([]) == 0
    assert "::warning::" in capsys.readouterr().out


def test_при_релизе_язык_отказ(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Публикация необратима, поэтому здесь запрет, а не предупреждение."""
    monkeypatch.setattr(
        changelog, "ROOT", _дерево(tmp_path, {"10.added.md": "In English.\n"})
    )

    assert changelog.main(["--strict"]) == changelog.EXIT_FAILED
    assert "::error::" in capsys.readouterr().out


def test_отсутствие_записи_находится(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        changelog, "ROOT", _дерево(tmp_path, {"10.added.md": "Запись.\n"})
    )

    код = changelog.main(
        ["--require-entry", "--changed", "src/claude_code_usage/cli.py"]
    )

    assert код == changelog.EXIT_FAILED
    assert "записи в changelog.d/ нет" in capsys.readouterr().out


def test_новая_запись_в_диффе_считается(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        changelog, "ROOT", _дерево(tmp_path, {"10.added.md": "Запись.\n"})
    )

    код = changelog.main(
        ["--require-entry", "--changed", "src/x.py", "changelog.d/10.added.md"]
    )

    assert код == 0


def test_запись_проекта_в_порядке() -> None:
    """Гейт прогоняется на самом репозитории, а не только на подделках."""
    фрагменты, претензии = changelog.collect(changelog.ROOT)

    assert претензии == []
    assert changelog.language_warnings(фрагменты) == []
    assert фрагменты, "в проекте должна быть хотя бы одна запись — эта"
