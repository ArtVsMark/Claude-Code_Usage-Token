"""Заслон от толчка в чужую ветку (правило 012, хук `PreToolUse`).

Дороже всего здесь **ложный отказ**: заслон, отвергающий законный толчок, чинят
не исправлением команды, а отключением хука — и тогда он не держит ничего.
Поэтому разрешённых случаев в наборе больше, чем запрещённых.

Второе по цене — **толчок за соединителем**: `cd … && git push origin main`.
Судить по первому слову строки значило бы не судить вовсе.
"""

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
        "git push origin HEAD:agent/работа",
        "git push origin agent/работа:agent/работа",
        "git push origin HEAD:refs/heads/agent/работа",
        "git push --tags origin",
        # Перенаправления — не аргументы толчка. Регрессию нашёл сам заслон на
        # живой команде окна: `… 2>&1 | grep` резался соединителем `&`, и
        # хвост `2>` судился как refspec.
        "git push -q origin agent/работа 2>&1 | grep -v remote",
        "git push origin agent/работа 2>/dev/null",
        "git push origin agent/работа > /tmp/вывод.txt",
        "git push origin agent/работа >>журнал 2>&1",
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
        # Цель — правая часть refspec (#155): прежде источник `HEAD` объявлял
        # толчок законным, куда бы он ни шёл, и `--force origin HEAD:main`
        # проходил с кодом 0 — `main` спасал только ruleset площадки (#151).
        ("git push origin HEAD:main", "main"),
        ("git push --force origin HEAD:main", "main"),
        ("git push origin HEAD:agent/другая", "agent/другая"),
        ("git push origin HEAD:refs/heads/agent/другая", "agent/другая"),
        ("git push origin :agent/другая", "agent/другая"),
        ("git push origin --delete agent/другая", "agent/другая"),
        # Толчок всех веток разом уносит и чужие.
        ("git push --all origin", "все ветки"),
        ("git push --mirror origin", "все ветки"),
    ],
)
def test_чужая_ветка_отвергается(команда: str, ждём: str) -> None:
    assert push_guard.чужая_ветка(команда, ГОЛОВА) == ждём


@pytest.mark.parametrize(
    "команда",
    ["git push", "git push origin", "git push origin main", "git push -u origin HEAD"],
)
def test_с_головы_main_не_толкают_никогда(команда: str) -> None:
    """`main` — общая ветка: окно в неё не толкает ни под каким именем."""
    assert push_guard.чужая_ветка(команда, "main") == "main"


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
