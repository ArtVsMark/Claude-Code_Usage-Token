"""Обёртка стража толчка: страж зовётся интерпретатором планки (#154, правило 217).

Голый `python3` окна ниже планки, и страж в её грамматике упал бы на нём
SyntaxError'ом с кодом 1 — площадка считает такой код неблокирующим, и толчок
ушёл бы без проверки, молча. Здесь проверено, что этого не происходит:

* планку `floor.sh` читает так же, как `tomllib` (разбор один — 214);
* каждая ступень поиска интерпретатора находит его сама, а не потому, что
  его нашла предыдущая: PATH в тестах собран из каталога, где лежат только
  нужные утилиты, и `python<планка>` в нём нет, пока тест его не положит;
* без интерпретатора толчок закрыт кодом 2, а прочие команды открыты.
"""

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

import py_style

КОРЕНЬ = Path(__file__).resolve().parents[1]
ХУКИ = КОРЕНЬ / ".claude" / "hooks"
ОБЁРТКА = ХУКИ / "push_guard.sh"
ПЛАНКА = f"{sys.version_info.major}.{sys.version_info.minor}"

#: Утилиты, без которых обёртка и страж не работают. Всё прочее в PATH теста
#: отсутствует — в том числе любой системный python.
УТИЛИТЫ = ("sh", "awk", "sed", "head", "cat", "dirname", "git")

pytestmark = pytest.mark.skipif(
    sys.platform == "win32" or any(shutil.which(у) is None for у in УТИЛИТЫ),
    reason="обёртка — sh-скрипт; на Windows её исполняет bash площадки",
)


def _планка_как_tomllib(манифест: Path) -> str:
    """Разбор на Python — единственный, гейта стиля (`py_style.планка`)."""
    мажор, минор = py_style.планка(манифест.parent)
    return f"{мажор}.{минор}"


def _планка_как_floor_sh(манифест: Path) -> str:
    итог = subprocess.run(
        ["sh", "-c", f'. "{ХУКИ / "floor.sh"}"; planka_floor "{манифест}"'],
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=10,
        check=True,
    )
    return итог.stdout.strip()


@pytest.mark.parametrize(
    "требование", ['">=3.14"', '">= 3.13"', '">=3.14,<4"', '">=3.15.1"']
)
def test_floor_sh_читает_планку_как_tomllib(tmp_path: Path, требование: str) -> None:
    манифест = tmp_path / "pyproject.toml"
    манифест.write_text(
        f'[project]\nname = "x"\nrequires-python = {требование}\n', encoding="utf-8"
    )
    assert _планка_как_floor_sh(манифест) == _планка_как_tomllib(манифест)


def test_floor_sh_на_настоящем_манифесте() -> None:
    манифест = КОРЕНЬ / "pyproject.toml"
    assert _планка_как_floor_sh(манифест) == _планка_как_tomllib(манифест)


# ── обёртка в изолированном PATH ─────────────────────────────────────────


def _проект(tmp_path: Path, планка: str) -> Path:
    """Проект с манифестом на заданной планке и git-головой `agent/x`."""
    проект = tmp_path / "проект"
    проект.mkdir()
    (проект / "pyproject.toml").write_text(
        f'[project]\nname = "x"\nrequires-python = ">={планка}"\n', encoding="utf-8"
    )
    for команда in (
        ["git", "init", "-q", "-b", "agent/x"],
        [
            "git",
            "-c",
            "user.name=т",
            "-c",
            "user.email=т@т",
            "commit",
            "-q",
            "--allow-empty",
            "-m",
            "x",
        ],
    ):
        subprocess.run(команда, cwd=проект, check=True, timeout=30)
    return проект


def _путь(tmp_path: Path, *, с_питоном: bool) -> str:
    """Каталог только с нужными утилитами; `python<планка>` — по запросу."""
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    for утилита in УТИЛИТЫ:
        (bin_ / утилита).symlink_to(shutil.which(утилита))  # type: ignore[arg-type]
    if с_питоном:
        (bin_ / f"python{ПЛАНКА}").symlink_to(sys.executable)
    return str(bin_)


def _позвать(проект: Path, путь: str, команда: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [shutil.which("sh") or "sh", str(ОБЁРТКА)],
        input=json.dumps({"tool_input": {"command": команда}}),
        cwd=проект,
        env={"PATH": путь, "CLAUDE_PROJECT_DIR": str(проект), "HOME": str(проект)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )


ЗАПРЕЩЁННЫЙ = "git push --force origin HEAD:main"
ЗАКОННЫЙ = "git push origin agent/x"


def test_интерпретатор_из_path_запускает_стража(tmp_path: Path) -> None:
    проект = _проект(tmp_path, ПЛАНКА)
    путь = _путь(tmp_path, с_питоном=True)

    отказ = _позвать(проект, путь, ЗАПРЕЩЁННЫЙ)
    assert отказ.returncode == 2
    assert "правило 012" in отказ.stderr, "отказал не страж, а запасной разбор"
    assert _позвать(проект, путь, ЗАКОННЫЙ).returncode == 0


def test_интерпретатор_из_venv_проекта_запускает_стража(tmp_path: Path) -> None:
    """Вторая ступень: в PATH интерпретатора нет, а в .venv проекта — на планке."""
    проект = _проект(tmp_path, ПЛАНКА)
    (проект / ".venv" / "bin").mkdir(parents=True)
    (проект / ".venv" / "bin" / "python").symlink_to(sys.executable)
    путь = _путь(tmp_path, с_питоном=False)

    отказ = _позвать(проект, путь, ЗАПРЕЩЁННЫЙ)
    assert отказ.returncode == 2
    assert "правило 012" in отказ.stderr, "venv на планке не нашёлся"


def test_venv_не_на_планке_не_годится(tmp_path: Path) -> None:
    """Окружение на другой версии — не интерпретатор планки."""
    проект = _проект(tmp_path, "9.99")
    (проект / ".venv" / "bin").mkdir(parents=True)
    (проект / ".venv" / "bin" / "python").symlink_to(sys.executable)
    путь = _путь(tmp_path, с_питоном=False)

    отказ = _позвать(проект, путь, ЗАКОННЫЙ)
    assert отказ.returncode == 2
    assert "страж толчка не запущен" in отказ.stderr


def test_без_интерпретатора_толчок_закрыт_остальное_открыто(tmp_path: Path) -> None:
    """Код 2, а не 1: ненулевой код кроме 2 площадка не считает отказом."""
    проект = _проект(tmp_path, "9.99")
    путь = _путь(tmp_path, с_питоном=False)

    толчок = _позвать(проект, путь, ЗАКОННЫЙ)
    assert толчок.returncode == 2
    assert "python9.99" in толчок.stderr
    assert _позвать(проект, путь, "ls -la").returncode == 0


def test_запасной_разбор_видит_толчок_за_опцией_git(tmp_path: Path) -> None:
    """`git -C путь push` — тоже толчок; литерал «git push» его пропускал."""
    проект = _проект(tmp_path, "9.99")
    путь = _путь(tmp_path, с_питоном=False)

    assert _позвать(проект, путь, "git -C . push").returncode == 2


def test_слово_git_в_пути_не_закрывает_команду(tmp_path: Path) -> None:
    """Берётся поле `command`, а не весь вход: `cwd` с `git` не в счёт."""
    проект = _проект(tmp_path, "9.99")
    путь = _путь(tmp_path, с_питоном=False)
    вход = json.dumps({"cwd": "/git/push", "tool_input": {"command": "ls"}})

    итог = subprocess.run(
        [shutil.which("sh") or "sh", str(ОБЁРТКА)],
        input=вход,
        cwd=проект,
        env={"PATH": путь, "CLAUDE_PROJECT_DIR": str(проект), "HOME": str(проект)},
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=30,
        check=False,
    )
    assert итог.returncode == 0


def test_настройки_зовут_обёртку() -> None:
    настройки = json.loads((КОРЕНЬ / ".claude" / "settings.json").read_text("utf-8"))
    команды = [
        h["command"]
        for запись in настройки["hooks"]["PreToolUse"]
        for h in запись["hooks"]
    ]
    assert any("push_guard.sh" in к for к in команды)
    assert os.access(ОБЁРТКА, os.X_OK), "обёртка не исполняемая"
