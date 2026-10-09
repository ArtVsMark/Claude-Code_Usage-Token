"""Отказ обязан быть громким.

`docs/roles.md`, роль 🧪 Тестировщика: ненулевой код возврата и сообщение,
которое называет, что именно не вышло. Молчаливый ноль был бы ложью и гейту, и
человеку.

С появлением `sample` (#2) «всё отказывает» перестало быть верным, и тест,
который это держал, переписан здесь же — он и был заведён с требованием упасть
в этот момент. Нереализованными остались `report` и `calibrate`.
"""

import pytest

from claude_code_usage import ISSUES_URL, cli


def test_без_команды_отказ_с_перечнем(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) != 0
    err = capsys.readouterr().err
    assert "не указана команда" in err
    for name in cli.COMMANDS:
        assert name in err


@pytest.mark.parametrize("name", sorted(cli._ISSUE_BY_COMMAND))
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
    # Полным адресом, а не «issue #1»: у поставившего пакет трекера под рукой
    # нет (076, находка #151).
    assert f"{ISSUES_URL}/" in err


@pytest.mark.parametrize("флаг", ["--help", "-h"])
def test_общая_справка_называет_каждую_команду(
    флаг: str, capsys: pytest.CaptureFixture[str]
) -> None:
    """Первое, что наберёт поставивший пакет, — `--help` (#174).

    Прежде он получал «неизвестная команда» и код 2.
    """
    assert cli.main([флаг]) == 0
    out = capsys.readouterr().out
    for name in cli.COMMANDS:
        assert f"  {name} " in out
        assert cli.PURPOSE[name] in out
    assert "ещё не реализована" in out


def test_у_каждой_команды_есть_назначение() -> None:
    """Команда без строки назначения уронила бы общую справку `KeyError`."""
    assert set(cli.PURPOSE) == set(cli.COMMANDS)


@pytest.mark.parametrize("name", ["sample", "probe"])
def test_справка_подкоманды_по_прежнему_своя(name: str) -> None:
    """Справку реализованной команды отдаёт её `argparse`, кодом 0."""
    with pytest.raises(SystemExit) as выход:
        cli.main([name, "--help"])
    assert выход.value.code == 0


def test_неизвестная_команда_отличима_от_нереализованной(
    capsys: pytest.CaptureFixture[str],
) -> None:
    assert cli.main(["сводка"]) != 0
    err = capsys.readouterr().err
    assert "неизвестная команда" in err
    assert "не реализована" not in err


@pytest.mark.parametrize("argv", [[], ["sample"], ["report"], ["calibrate"], ["чушь"]])
def test_вызов_без_работы_не_возвращает_ноль(argv: list[str]) -> None:
    """Прежняя редакция утверждала «отказывает ЛЮБОЙ вызов» и требовала себя
    переписать при появлении первой работающей команды. Момент настал: `sample`
    без источников по-прежнему отказ, но `sample --registry …` — уже работа.

    Свойство сузилось до проверяемого: вызов, которому нечего делать, ноль не
    возвращает. Голый `sample` остаётся в списке именно поэтому — источников
    он не назвал.
    """
    assert cli.main(argv) != 0


def test_каждая_команда_из_витрины_имеет_ответ() -> None:
    """Команда без реализации и без записи «не реализовано» отказала бы как
    опечатка — то есть посоветовала бы человеку не то.
    """
    реализованные = {"sample", "probe"}

    assert реализованные | set(cli._ISSUE_BY_COMMAND) == set(cli.COMMANDS)
