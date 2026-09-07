"""Один участник — одна подпись в конечной истории (правило 123, #94).

Дороже всего здесь **ложное «зелено» на пустоте**: прогоны берут дерево с
`fetch-depth: 1`, и `git log` там пуст не потому, что подписи в порядке.
Молчаливый ноль читался бы как «проверено и чисто», то есть гейт отвечал бы
зелёным, ничего не проверив, — ровно тот случай, от которого правила чтения
проверок в CLAUDE.md и заведены.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

import attribution

КОРЕНЬ = Path(__file__).resolve().parents[1]
GIT = shutil.which("git")
нужен_git = pytest.mark.skipif(GIT is None, reason="история читается git'ом")


def _репозиторий(tmp_path: Path, тела: list[str]) -> tuple[Path, str]:
    """Подделочный репозиторий: первый коммит — начало, остальные проверяются."""
    assert GIT is not None

    def git(*аргументы: str) -> str:
        return subprocess.run(
            (GIT, *аргументы),
            cwd=tmp_path,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
            timeout=30,
        ).stdout.strip()

    git("init", "-q", "-b", attribution.ВЕТКА)
    git("config", "user.email", "проба@example.com")
    git("config", "user.name", "Проба")
    (tmp_path / "файл").write_text("начало\n", encoding="utf-8")
    git("add", "-A")
    git("commit", "-q", "-m", "начало отсчёта")
    начало = git("rev-parse", "HEAD")

    for номер, тело in enumerate(тела, 1):
        (tmp_path / "файл").write_text(f"шаг {номер}\n", encoding="utf-8")
        git("add", "-A")
        git("commit", "-q", "-m", тело)
    return tmp_path, начало


ОДНА = "правка\n\nCo-authored-by: Claude <noreply@anthropic.com>"
ВТОРАЯ = (
    "правка\n\n"
    "Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>\n"
    "Co-authored-by: Claude <noreply@anthropic.com>"
)


@нужен_git
def test_одна_подпись_проходит(tmp_path: Path) -> None:
    корень, начало = _репозиторий(tmp_path, [ОДНА, ОДНА])
    итог = attribution.проверить(корень, начало=начало)
    assert итог.находки == []
    assert итог.коммитов == 2
    assert итог.имена == ["Claude"]


@нужен_git
def test_два_имени_одного_участника_отказ(tmp_path: Path) -> None:
    """Дословный признак нарушения правила 123."""
    корень, начало = _репозиторий(tmp_path, [ОДНА, ВТОРАЯ])
    итог = attribution.проверить(корень, начало=начало)
    assert len(итог.находки) == 1
    assert "под 2 именами" in итог.находки[0]
    assert итог.имена == ["Claude", "Claude Opus 5"]


@нужен_git
def test_история_до_начала_не_проверяется(tmp_path: Path) -> None:
    """Прошлое не переписывается: 33 коммита с двойной подписью уже уехали."""
    корень, _ = _репозиторий(tmp_path, [ВТОРАЯ])
    assert GIT is not None
    голова = subprocess.run(
        (GIT, "rev-parse", "HEAD"),
        cwd=корень,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    ).stdout.strip()
    итог = attribution.проверить(корень, начало=голова)
    assert итог.находки == []
    assert итог.коммитов == 0


@нужен_git
def test_неизвестное_начало_это_воздержание(tmp_path: Path) -> None:
    """Мелкий клон — не «чисто». Молчаливый ноль читался бы как проверенный."""
    корень, _ = _репозиторий(tmp_path, [ВТОРАЯ])
    итог = attribution.проверить(корень, начало="0" * 40)
    assert итог.истории_нет is True
    assert итог.находки == []


@нужен_git
def test_не_репозиторий_это_воздержание(tmp_path: Path) -> None:
    итог = attribution.проверить(tmp_path)
    assert итог.истории_нет is True


@нужен_git
def test_код_возврата(tmp_path: Path) -> None:
    корень, начало = _репозиторий(tmp_path, [ОДНА, ВТОРАЯ])
    assert attribution.проверить(корень, начало=начало).находки != []
    assert attribution.main([str(КОРЕНЬ)]) == 0


# ── ноль подписей и устаревшая ссылка (#94, вторая редакция) ────────────────

БЕЗ_ПОДПИСИ = "правка без единого трейлера"


@нужен_git
def test_ноль_подписей_это_находка(tmp_path: Path) -> None:
    """Ноль — не одна, и прежняя редакция этого не ловила.

    Находкой считалось только «имён больше одного», и щель уехала в общую ветку
    ТРЕМЯ коммитами подряд: очередь стала передавать площадке готовое тело, а
    та дописывает трейлер, только когда собирает его сама.
    """
    корень, начало = _репозиторий(tmp_path, [ОДНА, БЕЗ_ПОДПИСИ])
    итог = attribution.проверить(корень, начало=начало)
    assert len(итог.находки) == 1
    assert "без подписи участника: 1" in итог.находки[0]


@нужен_git
def test_ноль_и_двоение_называются_отдельно(tmp_path: Path) -> None:
    """Две разные болезни — два разных отказа."""
    корень, начало = _репозиторий(tmp_path, [БЕЗ_ПОДПИСИ, ВТОРАЯ])
    находки = attribution.проверить(корень, начало=начало).находки
    assert len(находки) == 2
    assert any("без подписи" in н for н in находки)
    assert any("под 2 именами" in н for н in находки)


@нужен_git
def test_устаревшая_ссылка_не_даёт_ложного_зелёного(tmp_path: Path) -> None:
    """Пусто здесь означало бы «ссылка не та», а не «после начала ничего нет».

    Замер 2026-09-07: локальная `main` отставала на четыре мержа, гейт напечатал
    «коммитов 0, имён 0» и прошёл, пока в общей ветке лежали три коммита без
    подписи.
    """
    корень, _ = _репозиторий(tmp_path, [БЕЗ_ПОДПИСИ])
    # Начало, которого в этой истории нет: измерять по такой ссылке нечего, и
    # ответ обязан быть «не видно», а не «чисто».
    итог = attribution.проверить(корень, начало="0" * 40)
    assert итог.истории_нет is True
    assert итог.находки == []
