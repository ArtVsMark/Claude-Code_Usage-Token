"""Отказ обязан быть громким.

`docs/roles.md`, роль 🧪 Тестировщика: ненулевой код возврата и сообщение,
которое называет, что именно не вышло. Пока не реализовано ничего, **любой**
вызов обязан отказать — молчаливый ноль здесь был бы ложью и гейту, и человеку.
"""

from __future__ import annotations

import pytest

from claude_code_usage import cli


def test_без_команды_отказ_с_перечнем(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) != 0
    err = capsys.readouterr().err
    assert "не указана команда" in err
    for name in cli.COMMANDS:
        assert name in err


@pytest.mark.parametrize("name", cli.COMMANDS)
def test_известная_команда_отказывает_называя_себя(
    name: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """«Ещё не реализовано» — тоже отказ, и он обязан назвать команду.

    Отдельно от неизвестной команды: человеку нужен разный следующий шаг —
    подождать реализации или исправить опечатку.
    """
    assert cli.main([name]) != 0
    err = capsys.readouterr().err
    assert name in err
    assert "не реализована" in err


def test_неизвестная_команда_отличима_от_нереализованной(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["сводка"]) != 0
    err = capsys.readouterr().err
    assert "неизвестная команда" in err
    assert "не реализована" not in err


@pytest.mark.parametrize("argv", [[], ["sample"], ["report"], ["calibrate"], ["чушь"]])
def test_ни_один_вызов_не_возвращает_ноль(argv: list[str]) -> None:
    """Главное свойство этого этапа, вынесенное отдельным тестом.

    Оно перестанет быть верным ровно тогда, когда появится первая работающая
    команда, — и тест обязан упасть в этот момент, потребовав себя переписать.
    Тест, который переживёт появление поведения незамеченным, ничего не держит.
    """
    assert cli.main(argv) != 0
