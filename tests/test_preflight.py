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


# ── паритет витрин ────────────────────────────────────────────────────────


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
