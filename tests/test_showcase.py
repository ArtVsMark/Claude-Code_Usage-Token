"""Проверки паритета витрин.

Отдельным файлом, а не хвостом `test_preflight.py`, по двум причинам.

Смысловая: паритет витрин — тема документации, а не механики команды; тесты
`preflight` про перечисление файлов, поиск секретов и коды возврата.

Практическая, и она из инцидента: две ветки, дописывавшие тесты в конец
одного файла, дали конфликт слияния ровно на этом хвосте — при том что сам
`preflight.py` слился чисто. Файл, в конец которого дописывают все, становится
точкой конфликта независимо от того, насколько независимы изменения.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import preflight


def _файл(каталог: Path, имя: str, текст: str) -> Path:
    путь = каталог / имя
    путь.parent.mkdir(parents=True, exist_ok=True)
    путь.write_text(текст, encoding="utf-8")
    return путь


def _витрины(каталог: Path, ru: str, en: str) -> Path:
    _файл(каталог, preflight.SHOWCASE_RU, ru)
    _файл(каталог, preflight.SHOWCASE_EN, en)
    return каталог


def test_витрины_сходятся(tmp_path: Path) -> None:
    корень = _витрины(
        tmp_path,
        "# Заголовок\n\n## Проблема\n\nТекст.\n",
        "# Title\n\n## The problem\n\nText.\n",
    )
    assert preflight.compare_showcases(корень) == []


def test_переключатель_языка_не_находка(tmp_path: Path) -> None:
    """Регрессия на ложное срабатывание, найденное на живых витринах.

    В `README.en.md` кириллица есть и она **законная**: подпись ссылки на
    русскую версию. Наивная проверка «кириллицы быть не должно» покраснела бы
    на правильной строке — и её выключили бы первой же правкой, вместе со всем
    остальным, что она ловит.
    """
    корень = _витрины(
        tmp_path,
        "# Заголовок\n\n> 🇬🇧 [English version](README.en.md)\n",
        "# Title\n\n> 🇷🇺 [Русская версия](README.md) · Unofficial tool.\n",
    )
    assert preflight.compare_showcases(корень) == []


def test_непереведённая_строка_находится(tmp_path: Path) -> None:
    корень = _витрины(
        tmp_path,
        "# Заголовок\n\n## Проблема\n\nТекст.\n",
        "# Title\n\n## The problem\n\nНепереведённый абзац.\n",
    )

    замечания = preflight.compare_showcases(корень)

    assert len(замечания) == 1
    assert "README.en.md:5" in замечания[0]
    assert "Непереведённый" in замечания[0]


def test_разъехавшаяся_структура_находится(tmp_path: Path) -> None:
    корень = _витрины(
        tmp_path,
        "# Заголовок\n\n## Проблема\n\n## Идея\n",
        "# Title\n\n## The problem\n",
    )

    замечания = preflight.compare_showcases(корень)

    assert len(замечания) == 1
    assert "структура заголовков разъехалась" in замечания[0]


def test_замечание_не_меняет_код_возврата(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Главное требование #11, проверенное на самом `main`, а не на отчёте.

    Расхождение витрин — состояние документации, а не дефект кода. Смешать его
    с отказами значило бы либо останавливать коммит из-за перевода, либо
    приучить пропускать красное.

    Список проверок подменён пустым: иначе `main` запустил бы `pytest`, то есть
    сам этот тест.
    """
    _витрины(tmp_path, "# А\n", "# A\n\nНепереведено.\n")
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    monkeypatch.setattr(preflight, "checks", tuple)
    monkeypatch.setattr(preflight, "tracked_files", list)

    код = preflight.main([])

    вывод = capsys.readouterr().err
    assert код == 0, "замечание не должно превращаться в отказ"
    assert "~ паритет витрин" in вывод
    assert "замечаний 1" in вывод


def test_отказ_остаётся_отказом_рядом_с_замечанием() -> None:
    """Замечание не должно и заглушать отказ."""
    итог = preflight.report(["mypy"], [("ruff check", "E501")], ["паритет витрин: 1"])
    assert "не прошло: ruff check" in итог
    assert "~ паритет витрин" in итог
    assert "замечаний 1" in итог
