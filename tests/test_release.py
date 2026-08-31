"""Проверки гейта выпуска (#12).

Публикация необратима: занятую версию нельзя выпустить заново даже после
удаления. Значит ложное «прошло» здесь дороже всего в проекте — оно означает
опубликованный пакет, в котором чего-то нет или что-то лишнее.

Ложное «не прошло» тоже стоит дорого, но иначе: выпуск останавливается, а
причина ищется в верном коде.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import release

КОРЕНЬ = Path(__file__).resolve().parents[1]

ПОЛНОЕ_КОЛЕСО = {
    "claude_code_usage/__init__.py": "__version__ = '0.1.0'\n",
    "claude_code_usage/cli.py": "def main(): ...\n",
    "claude_code_usage/py.typed": "",
    "claude_code_usage_meter-0.1.0.dist-info/METADATA": (
        "Metadata-Version: 2.4\nName: claude-code-usage-meter\nVersion: 0.1.0\n"
    ),
}


def _колесо(tmp_path: Path, содержимое: dict[str, str] | None = None) -> Path:
    путь = tmp_path / "пакет-0.1.0-py3-none-any.whl"
    with zipfile.ZipFile(путь, "w") as архив:
        for имя, текст in (ПОЛНОЕ_КОЛЕСО if содержимое is None else содержимое).items():
            архив.writestr(имя, текст)
    return путь


# ── версия из тега ────────────────────────────────────────────────────────


def test_версия_вынимается_из_тега() -> None:
    assert release.version_from_tag("v0.1.0") == "0.1.0"
    assert release.version_from_tag(" v10.20.30 ") == "10.20.30"


@pytest.mark.parametrize(
    "тег", ["0.1.0", "v0.1", "v0.1.0-rc1", "release-0.1.0", "v0.1.0.post1", ""]
)
def test_тег_не_той_формы_роняет(тег: str) -> None:
    """Свободная форма означала бы, что версию придётся угадывать разбором.

    Угаданная версия — та же ошибка, от которой заведена проверка.
    """
    with pytest.raises(release.TagFormatError):
        release.version_from_tag(тег)


def test_расхождение_тега_и_версии_находится() -> None:
    проблемы = release.check_version("v0.2.0", "0.1.0")
    assert len(проблемы) == 1
    assert "тег обещает 0.2.0" in проблемы[0]
    assert "необратима" in проблемы[0]


def test_совпадение_тега_и_версии_проходит() -> None:
    assert release.check_version("v0.1.0", "0.1.0") == []


# ── содержимое дистрибутива ───────────────────────────────────────────────


def test_полное_колесо_проходит(tmp_path: Path) -> None:
    assert release.check_wheel(_колесо(tmp_path), "0.1.0") == []


def test_колесо_без_py_typed(tmp_path: Path) -> None:
    """Без маркера строгость типов не уедет к тому, кто поставит пакет.

    Типы будут проигнорированы **молча** — ни ошибки, ни предупреждения.
    """
    без_маркера = {
        имя: текст
        for имя, текст in ПОЛНОЕ_КОЛЕСО.items()
        if not имя.endswith("py.typed")
    }

    проблемы = release.check_wheel(_колесо(tmp_path, без_маркера), "0.1.0")

    assert len(проблемы) == 1
    assert "py.typed" in проблемы[0]


def test_колесо_без_модуля(tmp_path: Path) -> None:
    """Раскладка src легко даёт пакет без модуля, и в репозитории это не видно."""
    без_cli = {
        имя: текст for имя, текст in ПОЛНОЕ_КОЛЕСО.items() if "cli.py" not in имя
    }

    проблемы = release.check_wheel(_колесо(tmp_path, без_cli), "0.1.0")

    assert len(проблемы) == 1
    assert "cli.py" in проблемы[0]


def test_тесты_внутри_колеса_находятся(tmp_path: Path) -> None:
    """Они уезжают к каждому, кто поставит пакет, и попадают в его имена."""
    с_тестами = dict(ПОЛНОЕ_КОЛЕСО, **{"tests/test_cli.py": "", "scripts/x.py": ""})

    проблемы = release.check_wheel(_колесо(tmp_path, с_тестами), "0.1.0")

    assert len(проблемы) == 1
    assert "лишнее" in проблемы[0]
    assert "tests/test_cli.py" in проблемы[0]


def test_metadata_с_другой_версией(tmp_path: Path) -> None:
    """То, что увидит устанавливающий, важнее того, что объявлено в дереве."""
    чужая = dict(
        ПОЛНОЕ_КОЛЕСО,
        **{
            "claude_code_usage_meter-0.1.0.dist-info/METADATA": (
                "Metadata-Version: 2.4\nName: x\nVersion: 9.9.9\n"
            )
        },
    )

    проблемы = release.check_wheel(_колесо(tmp_path, чужая), "0.1.0")

    assert len(проблемы) == 1
    assert "9.9.9" in проблемы[0]


def test_колеса_без_metadata(tmp_path: Path) -> None:
    без = {имя: т for имя, т in ПОЛНОЕ_КОЛЕСО.items() if "METADATA" not in имя}
    проблемы = release.check_wheel(_колесо(tmp_path, без), "0.1.0")
    assert any("нет METADATA" in п for п in проблемы)


def test_колеса_нет(tmp_path: Path) -> None:
    """Проверка, не нашедшая предмета, обязана сказать об этом."""
    проблемы = release.check_wheel(tmp_path / "нет.whl", "0.1.0")
    assert len(проблемы) == 1
    assert "колеса нет" in проблемы[0]


def test_битое_колесо(tmp_path: Path) -> None:
    путь = tmp_path / "битое.whl"
    путь.write_text("это не zip", encoding="utf-8")

    проблемы = release.check_wheel(путь, "0.1.0")

    assert len(проблемы) == 1
    assert "не читается" in проблемы[0]


# ── список обязательного не разошёлся с пакетом ───────────────────────────


def test_обязательное_существует_в_дереве() -> None:
    """Список обязательного проверяется на настоящем пакете, а не на выдумке.

    Модуль, переименованный или удалённый, оставил бы в списке имя, которого
    больше нет: гейт продолжил бы требовать его от колеса и краснел бы на
    верной сборке.
    """
    for путь in release.REQUIRED_IN_WHEEL:
        файл = КОРЕНЬ / "src" / путь
        assert файл.is_file(), f"{путь} объявлен обязательным, а в дереве его нет"


# ── прогон целиком ────────────────────────────────────────────────────────


def test_прогон_отказывает_ненулевым_кодом(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    код = release.main(["--tag", "v9.9.9", "--version", "0.1.0"])

    assert код == release.EXIT_FAILED
    вывод = capsys.readouterr()
    assert "::error::" in вывод.out
    assert "выпуск не готов" in вывод.err


def test_прогон_проходит_на_целом_колесе(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    код = release.main(
        ["--tag", "v0.1.0", "--version", "0.1.0", "--wheel", str(_колесо(tmp_path))]
    )

    assert код == 0
    assert "выпуск готов" in capsys.readouterr().out
