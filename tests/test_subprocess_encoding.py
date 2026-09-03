"""Кодировка подпроцесса названа явно (#63).

Подделки здесь — **строковые литералы**, а не настоящие вызовы: гейт ходит по
дереву под версией, и этот файл в нём лежит. Настоящий `text=True` без
кодировки в подделке стал бы находкой на самом дереве, `preflight` покраснел
бы на собственных тестах — и первым же действием их бы «починили», ослабив
гейт. Ровно эта ловушка уже была у переписи ссылок и у проверки на секреты.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import subprocess_encoding

КОРЕНЬ = Path(__file__).resolve().parents[1]


def _находки(исходник: str) -> list[str]:
    return [н.message for н in subprocess_encoding.check_text(исходник, "ф.py")]


def test_ровно_инцидент() -> None:
    """`text=True` без кодировки — то, на чём покраснели три ячейки из девяти."""
    assert len(_находки('subprocess.run(["git", "log"], text=True)')) == 1


def test_кодировка_названа_молчит() -> None:
    assert _находки('subprocess.run(["git"], text=True, encoding="utf-8")') == []


def test_байтовый_режим_не_трогается() -> None:
    """Без текстового режима кодировка не нужна: `stdout` — байты."""
    assert _находки('subprocess.run(["git"], capture_output=True)') == []
    assert _находки('subprocess.run(["git"], text=False)') == []


def test_errors_включает_текстовый_режим_тоже() -> None:
    """Шире буквы задачи — и это семантика CPython, а не расширение из аккуратности.

    Текстовый режим включает ЛЮБОЙ из `text`, `universal_newlines`, `errors`.
    То есть `errors="replace"` без `encoding=` берёт ту же локаль, а заметить
    это труднее: `errors` выглядит предусмотрительностью.
    """
    находки = _находки('subprocess.run(["git"], errors="replace")')

    assert len(находки) == 1
    assert "errors" in находки[0]


def test_universal_newlines_считается() -> None:
    """Старое написание того же флага; в чужом коде оно ещё встречается."""
    assert len(_находки("subprocess.Popen(cmd, universal_newlines=True)")) == 1


def test_encoding_none_это_та_же_локаль() -> None:
    """`encoding=None` — не «названа кодировка», а умолчание, записанное вслух."""
    assert len(_находки('subprocess.run(["git"], text=True, encoding=None)')) == 1


def test_kwargs_воздержание() -> None:
    """Кодировка может прийти из `**kwargs`, и заглянуть туда разбором нечем.

    Отказ здесь был бы догадкой, а гейт, догадывающийся о невидимом, теряет
    доверие раньше, чем ловит первый настоящий промах.
    """
    assert _находки('subprocess.run(["git"], text=True, **параметры)') == []


def test_неконстантный_флаг_краснеет() -> None:
    """Значение неизвестно — и гейт всё равно краснеет. Цена несимметрична.

    Ложный отказ стоит одного дописанного `encoding="utf-8"`, безвредного при
    любом значении флага. Пропуск стоит слепоты на трети матрицы, которая
    проявится через месяцы и не на всякой букве: на `замер` не падает, на
    `список` падает.
    """
    assert len(_находки('subprocess.run(["git"], text=флаг)')) == 1


def test_чужая_функция_с_тем_же_именем_аргумента_не_трогается() -> None:
    """Граница гейта: проверяются вызовы, похожие на subprocess, по имени функции."""
    assert _находки("нарисовать(подпись, text=True)") == []


def test_дерево_проекта_чистое() -> None:
    """Не подделка: настоящее дерево под версией.

    Замер 2026-09-03 до правки — две находки: `scripts/repo_links.py` и
    `tests/test_repo_links.py`. Обе с русским выводом git, обе на Windows.
    """
    результат = subprocess_encoding.check_tree(КОРЕНЬ)

    assert результат.находки == []
    assert результат.examined > 0, "гейт обязан назвать охват, а не только «чисто»"


def test_свой_обход_без_предмета_это_не_чисто(tmp_path: Path) -> None:
    """Гейт, не нашедший предмета своим обходом, обязан сказать об этом.

    Иначе переезд каталога тихо его отключит, и «чисто» будет означать
    «ничего не проверяли» — неотличимо от зелёного.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)

    результат = subprocess_encoding.check_tree(tmp_path)

    assert результат.находки != []
    assert "без предмета" in результат.находки[0].message


def test_чужое_перечисление_гейт_не_судит() -> None:
    """Пустой список от вызывающего — не поломка, и вердикта о ней тут нет.

    В `preflight` перечисление одно на все проверки, и подделочное дерево в
    тестах законно бывает без единого `.py`. Отказ здесь означал бы, что гейт
    судит о чужом перечислении, ничего о нём не зная; ответом остаётся охват,
    и его печатает вызывающий — «исходников 0» в имени проверки.
    """
    результат = subprocess_encoding.check_tree(КОРЕНЬ, files=[])

    assert результат.находки == []
    assert результат.examined == 0


def _прогон(cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(КОРЕНЬ / "scripts" / "subprocess_encoding.py")],
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def test_гейт_отдаёт_ненулевой_код(tmp_path: Path) -> None:
    """Гейт, который нельзя провалить, — не гейт (правило 075).

    Дерево настоящее, под версией: гейт по построению перечисляет файлы через
    git, и подделка, лежащая рядом с ним, но не добавленная, не проверялась бы
    вовсе — тест был бы зелёным на выключенном гейте.
    """
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    подделка = tmp_path / "плохо.py"
    подделка.write_text('subprocess.run(["git"], text=True)\n', encoding="utf-8")
    subprocess.run(["git", "add", "плохо.py"], cwd=tmp_path, check=True)

    ответ = _прогон(tmp_path)

    assert ответ.returncode == subprocess_encoding.EXIT_FAILED
    assert "плохо.py" in ответ.stdout


def test_неудача_перечисления_это_не_находка(tmp_path: Path) -> None:
    """«Прогнать не вышло» и «найден вызов без кодировки» — разные исходы.

    Найдено этим же тестом: вне git-репозитория `git ls-files` падает с кодом
    128, `CalledProcessError` не ловился, и трейсбек давал код 1 — ровно тот
    же, каким гейт сообщает о настоящей находке. Отказ, неотличимый от
    поломки, ведёт починку не туда.
    """
    ответ = _прогон(tmp_path)

    assert ответ.returncode == subprocess_encoding.EXIT_BROKEN
    assert "перечислить исходники не вышло" in ответ.stderr
