"""Хук старта облачного окна (#126).

Удачный путь здесь не проверяется: он ставит интерпретатор и ходит в PyPI, а
набор тестов в сеть не ходит. Проверяется то, что ломается молча: хук обязан
быть подключён рядом со сторожем толчка, а не вместо него, и вне облака не
делать ничего — на машине владельца ставить интерпретатор без спроса он не
вправе.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

КОРЕНЬ = Path(__file__).resolve().parents[1]
ХУК = КОРЕНЬ / ".claude" / "hooks" / "session-start.sh"
НАСТРОЙКИ = КОРЕНЬ / ".claude" / "settings.json"


def test_хук_подключён_рядом_со_сторожем_толчка() -> None:
    """`SessionStart` добавлен, `PreToolUse` не тронут."""
    хуки = json.loads(НАСТРОЙКИ.read_text(encoding="utf-8"))["hooks"]

    команды = [h["command"] for запись in хуки["SessionStart"] for h in запись["hooks"]]
    assert any("session-start.sh" in к for к in команды)
    сторож = [h["command"] for запись in хуки["PreToolUse"] for h in запись["hooks"]]
    # Страж зовётся ОБЁРТКОЙ интерпретатора планки, а не голым `python3`:
    # системный python3 окна ниже планки, и страж в её грамматике упал бы на
    # нём, а площадка сочла бы падение неблокирующим — толчок ушёл бы молча
    # (правило 217, #154).
    assert any("push_guard.sh" in к for к in сторож), "сторож толчка пропал"
    assert not any("python3" in к for к in сторож), "страж снова зовётся python3"


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="хук — bash-скрипт облачного окна (Linux); на Windows bash не тот",
)
def test_вне_облака_хук_ничего_не_делает(tmp_path: Path) -> None:
    """Признак облака снят — выход 0, файл окружения не тронут."""
    файл_окружения = tmp_path / "env"
    окружение = {
        **os.environ,
        "CLAUDE_CODE_REMOTE": "",
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "CLAUDE_ENV_FILE": str(файл_окружения),
    }

    итог = subprocess.run(
        ["bash", str(ХУК)],
        env=окружение,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert итог.returncode == 0
    assert not файл_окружения.exists()
    assert not (tmp_path / ".venv").exists()


@pytest.mark.skipif(
    sys.platform == "win32" or shutil.which("bash") is None,
    reason="хук — bash-скрипт облачного окна (Linux); на Windows bash не тот",
)
def test_непрочитанная_планка_не_роняет_старт(tmp_path: Path) -> None:
    """Сбой — предупреждение с названным шагом и выход 0, PATH не тронут.

    Каталог без `pyproject.toml` — дешёвый способ отказать первым же шагом,
    не выходя в сеть.
    """
    файл_окружения = tmp_path / "env"
    окружение = {
        **os.environ,
        "CLAUDE_CODE_REMOTE": "true",
        "CLAUDE_PROJECT_DIR": str(tmp_path),
        "CLAUDE_ENV_FILE": str(файл_окружения),
    }

    итог = subprocess.run(
        ["bash", str(ХУК)],
        env=окружение,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )

    assert итог.returncode == 0
    assert "планка requires-python не прочитана" in итог.stderr
    assert not файл_окружения.exists()
