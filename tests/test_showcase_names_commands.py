"""Витрина называет каждую команду, которую ставит пакет (правило 163, #146).

Пакет уходит наружу колесом выпуска, а вход у проекта один — витрины. Ответ
«механизмов наружу не отдаём» продержался, пока команда `claude-code-usage-meter`
не была названа ни в одной из них: витрина называла подкоманду `sample`, но не
то, чем её звать.

Предмет перечисляется машиной, а не суждением: каждое имя из
`[project.scripts]` обязано встретиться в `README.md` и `README.en.md`.

Чего тест не видит: КАК команда названа — с путём установки или мимоходом.
Это остаётся приёмке; здесь держится то, что решается данными целиком.
"""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ВИТРИНЫ = ("README.md", "README.en.md")


def команды(root: Path) -> list[str]:
    """Имена команд, которые ставит пакет, — из `[project.scripts]`."""
    with (root / "pyproject.toml").open("rb") as файл:
        проект = tomllib.load(файл).get("project", {})
    return sorted(проект.get("scripts", {}))


def не_названные(root: Path) -> list[str]:
    """Пары «витрина: команда», где команда не встречается словом целиком."""
    пропуски: list[str] = []
    for витрина in ВИТРИНЫ:
        текст = (root / витрина).read_text(encoding="utf-8")
        for имя in команды(root):
            # Целым словом: `x-meter` не должно засчитываться внутри `x-meter-old`.
            if re.search(rf"(?<![\w-]){re.escape(имя)}(?![\w-])", текст) is None:
                пропуски.append(f"{витрина}: {имя}")
    return пропуски


def _дерево(tmp_path: Path, ru: str, en: str) -> Path:
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\n[project.scripts]\nx-meter = "x.cli:main"\n',
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text(ru, encoding="utf-8")
    (tmp_path / "README.en.md").write_text(en, encoding="utf-8")
    return tmp_path


def test_команда_названа_в_обеих_витринах() -> None:
    assert команды(ROOT), "у пакета нет команд — проверке нечего проверять"
    assert not не_названные(ROOT), (
        f"витрина не называет команду пакета: {не_названные(ROOT)}. "
        "Пакет уходит наружу колесом выпуска, а вход у проекта — витрины (163)"
    )


def test_пропуск_в_одной_витрине_находится(tmp_path: Path) -> None:
    корень = _дерево(tmp_path, "Ставится `x-meter`.\n", "Install it.\n")
    assert не_названные(корень) == ["README.en.md: x-meter"]


def test_имя_внутри_другого_имени_не_засчитывается(tmp_path: Path) -> None:
    корень = _дерево(tmp_path, "`x-meter-old`\n", "`x-meter`\n")
    assert не_названные(корень) == ["README.md: x-meter"]
