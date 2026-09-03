"""Проверки гейта на вызовы подпроцесса без дедлайна (#95).

Почти все проверки здесь — про **границы**: где гейт обязан промолчать. Первая
редакция границ не знала и объявила находками `pr_check.run(…)` и
`merge_queue.run(…)` — свои функции с тем же именем. «Починка» дописала им
несуществующий параметр и уронила пятнадцать тестов, прежде чем это заметили.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import subprocess_timeout

КОРЕНЬ = Path(__file__).resolve().parents[1]

ШАПКА = "import subprocess\n"


def _находки(текст: str) -> list[str]:
    return [н.message for н in subprocess_timeout.check_text(текст, "п.py")]


# ── предмет ───────────────────────────────────────────────────────────────


def test_вызов_без_дедлайна_краснеет() -> None:
    находки = _находки(ШАПКА + 'subprocess.run(["git", "status"])\n')
    assert len(находки) == 1
    assert "без `timeout=`" in находки[0]


def test_вызов_с_дедлайном_чист() -> None:
    assert _находки(ШАПКА + 'subprocess.run(["git"], timeout=10)\n') == []


def test_дедлайн_переменной_годится() -> None:
    """Гейт требует названного значения, а не одинакового."""
    текст = ШАПКА + "ПОРОГ = 5\nsubprocess.run(['git'], timeout=ПОРОГ)\n"
    assert _находки(текст) == []


def test_явный_none_это_отсутствие_дедлайна() -> None:
    находки = _находки(ШАПКА + "subprocess.run(['git'], timeout=None)\n")
    assert len(находки) == 1


def test_все_функции_с_дедлайном_проверяются() -> None:
    for имя in sorted(subprocess_timeout.ФУНКЦИИ_С_ДЕДЛАЙНОМ):
        assert len(_находки(ШАПКА + f"subprocess.{имя}(['git'])\n")) == 1, имя


# ── границы: где гейт молчит ──────────────────────────────────────────────


def test_popen_не_предмет() -> None:
    """У конструктора `Popen` параметра `timeout` нет вовсе."""
    assert _находки(ШАПКА + "subprocess.Popen(['git'])\n") == []


def test_kwargs_воздержание() -> None:
    """Дедлайн может прийти из `**kwargs`, и заглянуть нечем."""
    текст = ШАПКА + "опции = {}\nsubprocess.run(['git'], **опции)\n"
    assert _находки(текст) == []


def test_своя_функция_run_не_предмет() -> None:
    """Ровно тот случай, на котором первая редакция дала ложную находку."""
    текст = "import pr_check\npr_check.run(fetch, ждём, attempts=9)\n"
    assert _находки(текст) == []


def test_голое_имя_без_импорта_не_предмет() -> None:
    assert _находки("run(['git'])\n") == []


# ── как назван subprocess в этом файле ────────────────────────────────────


def test_импорт_под_псевдонимом() -> None:
    текст = "import subprocess as sp\nsp.run(['git'])\n"
    assert len(_находки(текст)) == 1


def test_имя_взятое_из_модуля() -> None:
    текст = "from subprocess import run\nrun(['git'])\n"
    assert len(_находки(текст)) == 1


def test_имя_из_модуля_под_псевдонимом() -> None:
    текст = "from subprocess import run as запустить\nзапустить(['git'])\n"
    assert len(_находки(текст)) == 1


def test_чужой_модуль_с_тем_же_последним_звеном() -> None:
    """`asyncio.subprocess` — не наш `subprocess`, и `run` у него не тот."""
    текст = "import pr_check as subprocess_like\nsubprocess_like.run(['git'])\n"
    assert _находки(текст) == []


# ── охват и предмет ───────────────────────────────────────────────────────


def test_дерево_без_исходников_отказывает(tmp_path: Path) -> None:
    """«Предмета нет» — отказ: переезд каталога иначе выключил бы гейт молча."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True, timeout=30)

    итог = subprocess_timeout.check_tree(tmp_path)

    assert len(итог.находки) == 1
    assert "без предмета" in итог.находки[0].message


def test_пустой_переданный_список_не_отказ() -> None:
    """Изменение без исходников — это не «гейт остался без предмета»."""
    итог = subprocess_timeout.check_tree(КОРЕНЬ, files=[])
    assert итог.находки == []
    assert итог.examined == 0


def test_проект_чист() -> None:
    """Гейт на самом себе."""
    итог = subprocess_timeout.check_tree(КОРЕНЬ)
    assert итог.находки == [], "\n".join(str(н) for н in итог.находки)
    assert итог.examined > 0, "разобрано ноль файлов — проверять было нечего"
