"""Свои потоки говорят UTF-8, а не то, что решила локаль (#63).

Отдельным файлом, а не хвостом `test_preflight.py`: дом у функции теперь свой
— общий модуль, которым пользуются все гейты, а не одна команда. Тот же довод,
что развёл витрину и `preflight`: файл, в конец которого дописывают все,
становится точкой конфликта независимо от того, насколько независимы правки.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import utf8_output

КОРЕНЬ = Path(__file__).resolve().parents[1]


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

    utf8_output.force_utf8_output()

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
    utf8_output.force_utf8_output()


def test_кириллица_доходит_до_узкого_потока_читаемой(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверка читаемости, выполняемая **на той самой ОС**, где риск.

    Заменяет чтение лога прогона: логи заданий отдаются с хоста, закрытого
    политикой прокси, а этот тест едет в каждой ячейке матрицы, включая три
    ячейки Windows, и проверяет то же самое надёжнее — байтами, а не глазами.

    Без `force_utf8_output` запись кириллицы в поток с узкой кодировкой
    падает с `UnicodeEncodeError`, поэтому тест обязан упасть, если правку
    снимут.
    """
    import io

    буфер = io.BytesIO()
    поток = io.TextIOWrapper(буфер, encoding="ascii")
    monkeypatch.setattr("sys.stdout", поток)
    monkeypatch.setattr("sys.stderr", поток)

    utf8_output.force_utf8_output()

    строка = "✗ тесты (весь набор) — не прошло"
    поток.write(строка)
    поток.flush()

    assert буфер.getvalue().decode("utf-8") == строка


# ── гейт: скрипт обязан ставить UTF-8 первым делом ────────────────────────
#
# Подделки — строковые литералы исходника, а не файлы в `scripts/`: гейт ходит
# по этому каталогу, и настоящий скрипт-подделка покраснел бы на собственном
# дереве. Та же ловушка, что у переписи ссылок и у проверки на секреты.

_ЗАПУСК = 'if __name__ == "__main__":\n    raise SystemExit(main())\n'


def _находки(тело: str) -> list[str]:
    return [н.message for н in utf8_output.check_text(тело + _ЗАПУСК, "с.py")]


def test_скрипт_без_вызова_краснеет() -> None:
    """Ровно инцидент: семь скриптов печатали по-русски и кодировку не ставили."""
    находки = _находки("def main() -> int:\n    print('отказ')\n    return 1\n\n\n")

    assert len(находки) == 1
    assert "не зовёт" in находки[0]


def test_вызов_первым_делом_молчит() -> None:
    тело = (
        "def main() -> int:\n"
        "    force_utf8_output()\n"
        "    print('отказ')\n"
        "    return 1\n\n\n"
    )
    assert _находки(тело) == []


def test_вызов_после_печати_это_отказ() -> None:
    """Порядок, а не наличие: вызов после первой печати бесполезен.

    Найдено гейтом на собственном дереве, до того как он был дописан:
    `preflight` печатал отказ «не принимаю аргументов» раньше, чем ставил
    кодировку, — единственный отказ, который команда выдаёт до всякой работы.
    """
    тело = (
        "def main() -> int:\n"
        "    print('отказ')\n"
        "    force_utf8_output()\n"
        "    return 1\n\n\n"
    )
    находки = _находки(тело)

    assert len(находки) == 1
    assert "не первым делом" in находки[0]


def test_вызов_в_main_блоке_не_считается() -> None:
    """`main()` зовут и тесты, и другой код — тогда кодировки не будет.

    Так было в `preflight`: при запуске файлом вывод читаемый, при вызове
    `main([])` — нет, и разницу между двумя способами запуска не видно.
    """
    тело = "def main() -> int:\n    print('отказ')\n    return 1\n\n\n"
    хвост = 'if __name__ == "__main__":\n    force_utf8_output()\n    main()\n'
    находки = [н.message for н in utf8_output.check_text(тело + хвост, "с.py")]

    assert len(находки) == 1
    assert "вне main" in находки[0]


def test_библиотека_не_предмет() -> None:
    """Без блока запуска потоки не его: библиотеку никто не запускает процессом."""
    assert (
        utf8_output.check_text("def полезное() -> int:\n    return 1\n", "б.py") == []
    )


def test_скрипт_без_кириллицы_не_предмет() -> None:
    """Требование — про русский вывод, а не про форму `main`.

    Скрипт, печатающий только ASCII, от кодировки локали не страдает, и
    краснеть на нём значило бы наказывать за то, чего не случится.
    """
    тело = "def main() -> int:\n    print('ok')\n    return 0\n\n\n"
    assert _находки(тело) == []


def test_дерево_проекта_чистое() -> None:
    """Не подделка: настоящие `scripts/`.

    Замер 2026-09-03 до правки — семь скриптов без вызова вовсе, плюс три
    находки о порядке и месте вызова у тех, что его уже имели.
    """
    результат = utf8_output.check_tree(КОРЕНЬ)

    assert результат.находки == []
    assert результат.examined > 0, "гейт обязан назвать охват, а не только «чисто»"


def test_чужое_перечисление_гейт_не_судит() -> None:
    """Пустой список от вызывающего — законное состояние, а не поломка."""
    результат = utf8_output.check_tree(КОРЕНЬ, files=[])

    assert результат.находки == []
    assert результат.examined == 0


def test_гейт_отдаёт_ненулевой_код(tmp_path: Path) -> None:
    """Гейт, который нельзя провалить, — не гейт (правило 075)."""
    каталог = tmp_path / "scripts"
    каталог.mkdir()
    (каталог / "плохой.py").write_text(
        "def main() -> int:\n    print('отказ')\n    return 1\n\n\n" + _ЗАПУСК,
        encoding="utf-8",
    )

    ответ = subprocess.run(
        [
            sys.executable,
            str(КОРЕНЬ / "scripts" / "utf8_output.py"),
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
    )

    assert ответ.returncode == utf8_output.EXIT_FAILED
    assert "плохой.py" in ответ.stdout


# ── точка входа пакета (#69) ──────────────────────────────────────────────
#
# У неё нет блока `__main__`: обёртку делает установщик, а признака в самом
# файле не остаётся. Искать один признак у обоих родов запускаемого значило бы
# пропустить тот, чей вывод читает чужой человек, а не прогон.


def _пакет(корень: Path, тело: str, *, объявить: bool = True) -> Path:
    """Дерево с пакетом под `src/` и, если нужно, объявленной точкой входа."""
    # Имя пакета и имя команды — синтаксис, а он латиницей: голый ключ TOML
    # кириллицу не принимает вовсе, и подделка с ней проверяла бы не гейт, а
    # обработку неразобранного `pyproject.toml`.
    модуль = корень / "src" / "paket" / "cli.py"
    модуль.parent.mkdir(parents=True, exist_ok=True)
    модуль.write_text(тело, encoding="utf-8")
    объявление = '[project.scripts]\nmera = "paket.cli:main"\n' if объявить else ""
    (корень / "pyproject.toml").write_text(
        f'[project]\nname = "paket"\n{объявление}', encoding="utf-8"
    )
    (корень / "scripts").mkdir(exist_ok=True)
    return модуль


_БЕЗ_ВЫЗОВА = "def main() -> int:\n    print('отказ')\n    return 1\n"
_С_ВЫЗОВОМ = (
    "def main() -> int:\n    force_utf8_output()\n    print('отказ')\n    return 1\n"
)


def test_точка_входа_без_вызова_краснеет(tmp_path: Path) -> None:
    """Ровно #69: у `cli.py` нет блока `__main__`, и гейт его не видел."""
    _пакет(tmp_path, _БЕЗ_ВЫЗОВА)

    результат = utf8_output.check_tree(tmp_path)

    assert len(результат.находки) == 1
    assert "cli.py" in результат.находки[0].path
    assert результат.examined == 1


def test_точка_входа_с_вызовом_молчит(tmp_path: Path) -> None:
    _пакет(tmp_path, _С_ВЫЗОВОМ)

    assert utf8_output.check_tree(tmp_path).находки == []


def test_модуль_пакета_без_объявления_не_предмет(tmp_path: Path) -> None:
    """Библиотечный модуль пакета потоками не владеет — как и любой другой.

    Без этого различия гейт требовал бы кодировку от каждого файла пакета, и
    его сняли бы первой же правкой вместе со всем, что он ловит.
    """
    _пакет(tmp_path, _БЕЗ_ВЫЗОВА, объявить=False)
    # Законный скрипт рядом — иначе дерево осталось бы вовсе без предмета, и
    # гейт ответил бы отказом о пустом обходе, а тест не про это.
    (tmp_path / "scripts" / "хороший.py").write_text(
        "def main() -> int:\n"
        "    force_utf8_output()\n"
        "    print('готово')\n"
        "    return 0\n\n\n" + _ЗАПУСК,
        encoding="utf-8",
    )

    результат = utf8_output.check_tree(tmp_path)

    assert результат.находки == []
    assert результат.examined == 1, "проверен скрипт, а модуль пакета — не предмет"


def test_объявление_читается_из_pyproject(tmp_path: Path) -> None:
    """Раскладка пакета не вшита в гейт вторым местом: она берётся из объявления."""
    _пакет(tmp_path, _С_ВЫЗОВОМ)

    точки = utf8_output.entry_points(tmp_path)

    assert [п.name for п in точки] == ["cli.py"]


def test_пакет_проекта_в_охвате() -> None:
    """Не подделка: настоящее объявление проекта.

    Замер 2026-09-03: точка входа одна — `claude-code-usage-meter`, и до #69
    она кодировку не ставила, а гейт её не видел вовсе.
    """
    точки = utf8_output.entry_points(КОРЕНЬ)

    assert [п.name for п in точки] == ["cli.py"]
