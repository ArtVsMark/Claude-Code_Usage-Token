"""Заслон от толчка в чужую ветку (правило 012, хук `PreToolUse`).

Дороже всего здесь **ложный отказ**: заслон, отвергающий законный толчок, чинят
не исправлением команды, а отключением хука — и тогда он не держит ничего.
Поэтому разрешённых случаев в наборе больше, чем запрещённых.

Второе по цене — **толчок за соединителем**: `cd … && git push origin main`.
Судить по первому слову строки значило бы не судить вовсе.
"""

from __future__ import annotations

import push_guard
import pytest

ГОЛОВА = "agent/работа"


@pytest.mark.parametrize(
    "команда",
    [
        "git push",
        "git push origin",
        "git push -u origin agent/работа",
        "git push --force-with-lease origin agent/работа",
        "git push origin HEAD:main",
        "git push origin HEAD:agent/другая",
        "git status",
        "git fetch origin main",
        "echo git push origin main",
    ],
)
def test_законное_пропускается(команда: str) -> None:
    """Ложный отказ дороже пропуска: его чинят отключением хука."""
    assert push_guard.чужая_ветка(команда, ГОЛОВА) is None


@pytest.mark.parametrize(
    ("команда", "ждём"),
    [
        ("git push origin main", "main"),
        ("git push -f origin main", "main"),
        ("git push origin +main", "main"),
        ("git push origin agent/чужая", "agent/чужая"),
        ("git push origin agent/чужая:main", "agent/чужая"),
        ("cd /tmp && git push origin main", "main"),
        ("git fetch origin && git push origin main", "main"),
    ],
)
def test_чужая_ветка_отвергается(команда: str, ждём: str) -> None:
    assert push_guard.чужая_ветка(команда, ГОЛОВА) == ждём


def test_без_головы_не_судим() -> None:
    """Отсоединённая голова или не репозиторий: сравнивать не с чем."""
    assert push_guard.чужая_ветка("git push origin main", None) is None


def test_неразбираемая_строка_не_судится() -> None:
    """Незакрытая кавычка — не повод отвергать: это не наш предмет."""
    assert push_guard.чужая_ветка("git push origin 'main", ГОЛОВА) is None


def test_эхо_первого_слова_не_обманывает() -> None:
    """`echo` — не толчок, даже если дальше стоит push."""
    assert push_guard.чужая_ветка("echo 'git push origin main'", ГОЛОВА) is None


# ── ложный отказ, случившийся на живой команде ──────────────────────────────


ЖИВАЯ_КОМАНДА = (
    "git add -A && git commit -q -F - <<'MSG' && git push 2>&1|tail -1\n"
    "docs: свод называет команду\n"
    "\n"
    "Толкается то, над чем идёт работа: `git push origin ветка`.\n"
    "MSG"
)


def test_heredoc_не_судится() -> None:
    """Тело heredoc — проза, а не аргументы, и разбору они неотличимы.

    Живой замер: эта команда была объявлена толчком в ветку «docs» — первое
    слово заголовка коммита, попавшее в аргументы вместе со всем сообщением.
    Ложный отказ чинят отключением хука, а не исправлением команды.
    """
    assert push_guard.чужая_ветка(ЖИВАЯ_КОМАНДА, ГОЛОВА) is None


def test_труба_без_пробелов_разделяет_команды() -> None:
    """`shlex` отдаёт `2>&1|tail` одним словом, и команда за трубой прежде
    оставалась приклеенной к предыдущей."""
    assert push_guard.чужая_ветка("git push origin main|tee log", ГОЛОВА) == "main"
    assert push_guard.чужая_ветка("git log|head && git push", ГОЛОВА) is None


def test_перевод_строки_разделяет_команды() -> None:
    assert push_guard.чужая_ветка("git status\ngit push origin main", ГОЛОВА) == "main"
