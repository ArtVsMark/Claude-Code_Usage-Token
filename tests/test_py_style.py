"""Гейт стиля на планке (#154, правило 217): находит отставшее и молчит на законном."""

import subprocess
from pathlib import Path

import pytest

import py_style

КОРЕНЬ = Path(__file__).resolve().parents[1]
П = (3, 14)


def test_future_annotations_находится() -> None:
    текст = '"""Док."""\n\nfrom __future__ import annotations\n\nx = 1\n'
    находки = py_style.check_text(текст, "a.py", П)
    assert [(н.line, "PEP 649" in н.message) for н in находки] == [(3, True)]


@pytest.mark.parametrize(
    "текст",
    [
        "try:\n    pass\nexcept (ValueError, OSError):\n    pass\n",
        "try:\n    pass\nexcept* (ValueError, OSError):\n    pass\n",
    ],
)
def test_скобки_в_except_находятся(текст: str) -> None:
    assert [н.line for н in py_style.check_text(текст, "a.py", П)] == [3]


@pytest.mark.parametrize(
    "текст",
    [
        "try:\n    pass\nexcept (ValueError, OSError) as e:\n    pass\n",
        "try:\n    pass\nexcept (ValueError,):\n    pass\n",
        "try:\n    pass\nexcept ValueError, OSError:\n    pass\n",
        's = "except (A, B):"\n# except (A, B):\n',
    ],
)
def test_законное_молчит(текст: str) -> None:
    assert py_style.check_text(текст, "a.py", П) == []


def test_ниже_планки_требования_не_действуют() -> None:
    """Цель из планки: на 3.13 скобки и `__future__` законны (005)."""
    текст = (
        "from __future__ import annotations\ntry:\n    pass\nexcept (A, B):\n    pass\n"
    )
    assert py_style.check_text(текст, "a.py", (3, 13)) == []


def _манифест(tmp_path: Path, хвост: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nrequires-python = ">=3.14"\n' + хвост,
        encoding="utf-8",
    )
    return tmp_path


def test_цель_ruff_не_с_планки_находится(tmp_path: Path) -> None:
    корень = _манифест(tmp_path, '[tool.ruff]\ntarget-version = "py313"\n')
    assert "py314" in py_style.check_ruff(корень, П)[0].message


def test_исключение_по_файлам_находится(tmp_path: Path) -> None:
    корень = _манифест(
        tmp_path,
        '[tool.ruff]\ntarget-version = "py314"\n'
        '[tool.ruff.per-file-target-version]\n".claude/hooks/*.py" = "py311"\n',
    )
    находки = py_style.check_ruff(корень, П)
    assert len(находки) == 1
    assert "Исключений нет" in находки[0].message


def test_дерево_на_планке() -> None:
    файлы = [
        КОРЕНЬ / путь
        for путь in subprocess.run(
            ["git", "ls-files", "-z", "*.py"],
            cwd=КОРЕНЬ,
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
            check=True,
        ).stdout.split("\0")
        if путь
    ]
    итог = py_style.check_tree(КОРЕНЬ, files=файлы)
    assert итог.файлов >= 70, "у проверки должен быть предмет (075)"
    assert итог.находки == [], [str(н) for н in итог.находки]
