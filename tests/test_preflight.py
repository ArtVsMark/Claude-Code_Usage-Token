"""Проверки самого `preflight`.

Здесь намеренно **не** вызывается полный прогон: `main()` запускает `pytest`,
и тест, который его вызовет, запустит сам себя. Проверяются составные части —
поиск секретов, сборка итога, отказ на аргументах.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import preflight

# Литералы шаблонов собраны из частей ровно по той же причине, что и в самом
# `preflight`: иначе этот файл станет находкой собственной проверки.
ТОКЕН_GITHUB = "gh" + "p_" + "A" * 36
КЛЮЧ_ANTHROPIC = "sk" + "-ant-" + "B" * 30
# Тот же приём: целиком записанный заголовок сделал бы находкой этот файл.
ЗАКРЫТЫЙ_КЛЮЧ = "-----BEGIN " + "RSA PRIVATE KEY-----"


def _файл(каталог: Path, имя: str, текст: str) -> Path:
    путь = каталог / имя
    путь.parent.mkdir(parents=True, exist_ok=True)
    путь.write_text(текст, encoding="utf-8")
    return путь


def test_чистый_текст_не_находка(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    путь = _файл(tmp_path, "чисто.py", "def main() -> int:\n    return 0\n")
    assert preflight.scan_for_secrets([путь]).findings == []


@pytest.mark.parametrize(
    ("секрет", "ожидаемое"),
    [
        (ТОКЕН_GITHUB, "токен GitHub"),
        (КЛЮЧ_ANTHROPIC, "ключ Anthropic"),
        ("AKIA" + "C" * 16, "ключ AWS"),
        (ЗАКРЫТЫЙ_КЛЮЧ, "закрытый ключ"),
    ],
)
def test_секрет_находится_и_называется(
    секрет: str, ожидаемое: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Находка обязана называть, что именно нашлось, и где.

    «Найден секрет» без имени и строки заставляет искать глазами — то есть
    возвращает ту же ручную работу, которую команда заменяет.
    """
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    путь = _файл(tmp_path, "плохо.py", f"# первая строка\nТОКЕН = '{секрет}'\n")

    находки = preflight.scan_for_secrets([путь]).findings

    assert len(находки) == 1
    assert ожидаемое in находки[0]
    assert "плохо.py:2" in находки[0]


def test_файл_замеров_находка_сам_по_себе(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """`*.jsonl` в репозитории кода — находка независимо от содержимого.

    Замер выглядит безобидно: несколько чисел. Ряд таких строк — распорядок
    дня и стоимость работы, поэтому «обезличенный» файл замеров тоже нельзя.
    """
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    путь = _файл(tmp_path, "samples/usage.jsonl", '{"ts":"2026-08-21T09:10:03Z"}\n')

    находки = preflight.scan_for_secrets([путь]).findings

    assert len(находки) == 1
    assert "samples/usage.jsonl" in находки[0]
    assert "приватном хранилище" in находки[0]


def test_двоичный_файл_не_роняет_проверку(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Нечитаемый файл — не находка и не отказ.

    Иначе первый же PNG в репозитории превратит проверку в постоянно красную,
    и её выключат целиком.
    """
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    путь = tmp_path / "картинка.png"
    путь.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe\x00")
    assert preflight.scan_for_secrets([путь]).findings == []


def test_проверка_не_находит_саму_себя() -> None:
    """Главная защита от ложного срабатывания, вынесенная отдельным тестом.

    Шаблоны в `preflight` разбиты символьным классом именно для этого. Если
    кто-то «упростит» `gh[p]_` до `ghp_`, прогон покраснеет на собственном
    исходнике — и проверку выключат как ложную. Тест обязан упасть раньше.
    """
    свои = [
        Path(preflight.__file__),
        Path(__file__),
    ]
    assert preflight.scan_for_secrets(свои).findings == []


def test_итог_называет_отказавшее_по_имени() -> None:
    итог = preflight.report(["тесты", "mypy"], [("ruff check", "E501 ...")])
    assert "не прошло: ruff check" in итог
    assert "✗ ruff check" in итог
    assert "✓ тесты" in итог


def test_итог_без_отказов_не_врёт() -> None:
    итог = preflight.report(["тесты", "mypy"], [])
    assert "не прошло" not in итог
    assert "всё чисто" in итог


def test_все_проверки_чек_листа_на_месте() -> None:
    """Состав обязан совпадать с чек-листом, иначе команда его не заменяет."""
    имена = " ".join(c.name for c in preflight.checks())
    for ожидаемое in ("тесты", "ruff check", "ruff format", "mypy"):
        assert ожидаемое in имена
    assert all(c.argv[0] for c in preflight.checks())


def test_аргументы_отвергаются_громко(capsys: pytest.CaptureFixture[str]) -> None:
    """У команды нет аргументов, и молча их игнорировать нельзя.

    Человек, написавший `preflight --fix`, обязан узнать, что ничего не
    починилось, а не решить, что починилось.
    """
    assert preflight.main(["--fix"]) != 0
    assert "не принимает аргументов" in capsys.readouterr().err


def test_кириллическое_имя_файла_не_выпадает(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Регрессия на настоящий дефект, найденный прогоном этой же команды.

    `git ls-files` **без `-z`** экранирует не-ASCII имена
    (`"\\321\\203..."`), путь не разрешается, файл молча выпадает из
    проверки. Внутренний язык проекта русский, поэтому кириллица в имени —
    обычное дело: слепая зона приходилась ровно на то, что пишут чаще всего.

    Дефект прятался вдвойне — нечитаемый путь пропускался молча.
    """
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    путь = _файл(tmp_path, "утечка.py", f'ТОКЕН = "{ТОКЕН_GITHUB}"\n')

    результат = preflight.scan_for_secrets([путь])

    assert len(результат.findings) == 1
    assert "утечка.py" in результат.findings[0]
    assert результат.examined == 1


def test_перечисление_отдаёт_существующие_пути() -> None:
    """`tracked_files` обязан возвращать пути, которые действительно есть.

    Именно это и сломалось: экранированное имя давало путь, которого нет, и
    файл пропадал из проверки без единого слова.
    """
    файлы = preflight.tracked_files()

    assert файлы, "перечисление не должно быть пустым в рабочем репозитории"
    отсутствующие = [p for p in файлы if not p.exists()]
    assert отсутствующие == [], f"пути не разрешились: {отсутствующие[:5]}"


def test_охват_попадает_в_вывод(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """«Чисто» обязано сопровождаться числом просмотренного.

    Без охвата «чисто» неотличимо от «ничего не проверяли» — а это ровно то
    состояние, в котором проверка находилась, пока имена выпадали.
    """
    monkeypatch.setattr(preflight, "ROOT", tmp_path)
    текст = _файл(tmp_path, "чисто.py", "x = 1\n")
    двоичный = tmp_path / "картинка.png"
    двоичный.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe")

    результат = preflight.scan_for_secrets([текст, двоичный])

    assert результат.findings == []
    assert результат.examined == 1
    assert результат.skipped == 1


def test_потоки_переводятся_в_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отчёт обязан оставаться читаемым при узкой кодировке потока.

    На Windows кодировка берётся из локали и UTF-8 не является. Падения нет:
    поток заменяет непредставимое молча — и команда, смысл которой в том, чтобы
    **назвать** отказавшее, перестаёт называть что-либо, оставаясь формально
    работающей. Это хуже отказа: отказ виден.
    """
    import io

    узкий_вывод = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    узкий_отказ = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr("sys.stdout", узкий_вывод)
    monkeypatch.setattr("sys.stderr", узкий_отказ)

    preflight.force_utf8_output()

    assert узкий_вывод.encoding == "utf-8"
    assert узкий_отказ.encoding == "utf-8"


def test_перевод_потоков_переживает_подменённый_поток(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Поток без `reconfigure` не должен ронять команду.

    Под перехватом вывода (pytest, некоторые обёртки CI) `sys.stdout` — не
    файловый поток, и метода у него нет. Падать из-за этого нельзя: проверки
    важнее кодировки.
    """
    monkeypatch.setattr("sys.stdout", object())
    monkeypatch.setattr("sys.stderr", object())
    preflight.force_utf8_output()
