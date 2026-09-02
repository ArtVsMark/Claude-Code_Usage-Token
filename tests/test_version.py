"""Версия считается по истории, а не правится на глазок.

Замер, породивший этот механизм: с `v0.1.0` было принято **двенадцать**
изменений, а значок и пакет по-прежнему показывали `0.1.0`. Никто не заметил,
потому что заметить было нечем — цифры не было нигде.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import version


def _git(где: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(где), *args], check=True, capture_output=True)


@pytest.fixture
def репозиторий(tmp_path: Path) -> Path:
    """Настоящий git с настоящими тегами: схема живёт в истории, не в подделке."""
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    _git(tmp_path, "config", "user.email", "t@e.st")
    _git(tmp_path, "config", "user.name", "Тест")
    (tmp_path / "src" / "claude_code_usage").mkdir(parents=True)
    (tmp_path / version.VERSION_PATH).write_text(
        '__version__ = "0.1.0"\n', encoding="utf-8"
    )
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-qm", "первый")
    _git(tmp_path, "tag", "v0.1.0")
    return tmp_path


def _изменение(репо: Path, тема: str) -> None:
    (репо / "файл.txt").write_text(тема, encoding="utf-8")
    _git(репо, "add", "-A")
    _git(репо, "commit", "-qm", тема)


# ── счёт ──────────────────────────────────────────────────────────────────


def test_сразу_после_тега_счёт_нулевой(репозиторий: Path) -> None:
    assert version.counted(репозиторий) == ("v0.1.0", "0.1.0", 0)


def test_каждое_принятое_изменение_даёт_плюс_один(репозиторий: Path) -> None:
    _изменение(репозиторий, "feat: первое (#10)")
    _изменение(репозиторий, "fix: второе (#11)")

    assert version.counted(репозиторий) == ("v0.1.0", "0.1.2", 2)


def test_одно_изменение_в_нескольких_коммитах_считается_раз() -> None:
    """Считаются сущности, а не рёбра графа: дробление не должно завышать счёт."""
    темы = ["feat: часть (#10)", "fix: та же задача (#10)", "docs: и ещё (#10)"]

    assert version.pr_numbers(темы) == {"10"}


def test_обычный_мерж_тоже_несёт_номер() -> None:
    """Уплотнённый мерж даёт `(#N)`, обычный — `Merge pull request #N`."""
    assert version.pr_numbers(["Merge pull request #58 from ArtVsMark/x"]) == {"58"}


def test_склейка_git_pull_изменением_не_является() -> None:
    """Форма истории зависит от окна, а не от того, что сделано."""
    assert not version.countable("Merge branch 'main' of github.com:o/r")
    assert not version.countable("Merge remote-tracking branch 'origin/main'")
    assert version.countable("feat: прямой пуш без номера")


def test_безномерной_прямой_пуш_считается(репозиторий: Path) -> None:
    """Он реален: изменение уехало, номера у него просто нет."""
    _изменение(репозиторий, "docs: правка без номера")

    assert version.counted(репозиторий) == ("v0.1.0", "0.1.1", 1)


# ── третий исход ──────────────────────────────────────────────────────────


def test_без_тега_версия_недостоверна_а_не_ноль(tmp_path: Path) -> None:
    """Так клонирует облачное окно и `actions/checkout` без `fetch-depth: 0`.

    Правдоподобная цифра здесь хуже отказа: она выглядела бы свежей. Правило
    039 — у проверки три исхода, а не два.
    """
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        check=True,
        capture_output=True,
    )

    assert version.counted(tmp_path) is None


def test_отказ_называет_что_делать(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Отказ без следующего шага заставляет искать причину с нуля.

    Код отдельный: «версия недостоверна» — это не «проверка нашла проблему»,
    и человеку нужен разный следующий шаг.
    """
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        check=True,
        capture_output=True,
    )
    monkeypatch.setattr(version, "ROOT", tmp_path)

    код = version.main([])

    assert код == version.EXIT_UNKNOWN
    err = capsys.readouterr().err
    assert "git fetch --tags" in err
    assert "недостоверна" in err


def test_тег_без_префикса_под_схему_не_подпадает(репозиторий: Path) -> None:
    """Тег без `v` — не тег схемы. Отсекается маской ещё до разбора."""
    _изменение(репозиторий, "feat: после (#10)")
    _git(репозиторий, "tag", "0.9.0")

    тег, _, _ = version.counted(репозиторий)  # type: ignore[misc]
    assert тег == "v0.1.0"


def test_тег_похожий_но_не_по_схеме_делает_версию_недостоверной(
    репозиторий: Path,
) -> None:
    """Маска пропускает `v1.2.3-rc1`, а схема — нет, и форма проверяется дважды.

    Принять такой тег значило бы объявить версию, которой схема не знает.
    Правильный ответ — третий исход: недостоверно, а не «примерно 1.2».
    """
    _изменение(репозиторий, "feat: после (#10)")
    _git(репозиторий, "tag", "v1.2.3-rc1")

    assert version.counted(репозиторий) is None


def test_внутренние_коммиты_слитой_ветки_не_считаются_поштучно(
    репозиторий: Path,
) -> None:
    """Дробление не должно завышать счёт, а форма истории — влиять на номер.

    Ветка из двух безномерных коммитов, влитая мержем с номером, — это ОДНО
    принятое изменение. Если безномерные брать по всей истории, а не с
    first-parent линии, выйдет три.
    """
    _git(репозиторий, "checkout", "-q", "-b", "ветка")
    _изменение(репозиторий, "wip: первый шаг")
    _изменение(репозиторий, "wip: второй шаг")
    _git(репозиторий, "checkout", "-q", "main")
    _git(
        репозиторий,
        "merge",
        "--no-ff",
        "-q",
        "-m",
        "Merge pull request #10 from ArtVsMark/ветка",
        "ветка",
    )

    assert version.counted(репозиторий) == ("v0.1.0", "0.1.1", 1)


# ── сверка выпущенной версии ──────────────────────────────────────────────


def test_литерал_равный_тегу_проходит(репозиторий: Path) -> None:
    assert version.check(репозиторий) == []


def test_расхождение_литерала_и_тега_находится(репозиторий: Path) -> None:
    """Инвариант схемы — каждый тег `vX.Y.0`, значит между выпусками они равны.

    Расхождение означает либо поднятую и не выпущенную версию, либо забытый
    подъём; и то и другое кончается выпуском не под тем номером.
    """
    (репозиторий / version.VERSION_PATH).write_text(
        '__version__ = "0.2.0"\n', encoding="utf-8"
    )

    беды = version.check(репозиторий)

    assert len(беды) == 1
    assert "0.2.0" in беды[0] and "v0.1.0" in беды[0]


def test_без_тега_сверять_нечего(tmp_path: Path) -> None:
    """Не отказ: тега нет — значит и утверждать нечего."""
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        check=True,
        capture_output=True,
    )

    assert version.check(tmp_path) == []


# ── дерево проекта ────────────────────────────────────────────────────────


def test_выпущенная_версия_читается_из_одного_места() -> None:
    """`pyproject.toml` берёт её оттуда же — правок в двух местах не бывает."""
    assert version.released_version() == "0.1.0"
