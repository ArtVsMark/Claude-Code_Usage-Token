"""Граница предполётной: каждый скрипт прогонов либо прогоняется, либо назван.

Состав предполётной — сам `scripts/preflight.py`, а гейты зовут и другие
прогоны. Скрипт, который прогон зовёт, а предполётная не импортирует и не
называет, — проверка, краснеющая только у площадки. Так жил журнал: формат и
язык записей судил лишь прогон `changelog` (#158, состав из `ci.yml`).

Чего тест не видит: импорт ради вспомогательной функции засчитывается как
прогон. `check_pr_metadata` импортирован ради приставки ветки и потому
обязан стоять в реестре площадки явно — реестр сильнее импорта.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import preflight

ROOT = Path(__file__).resolve().parent.parent
WORKFLOWS = ROOT / ".github" / "workflows"

#: Вызов СВОЕГО скрипта: `python scripts/<имя>.py`. Чужие (`.rules-catalogue/
#: scripts/…`) сюда не попадают: их гейт — в своём проекте.
_ВЫЗОВ = re.compile(r"(?<![\w./-])python3? scripts/(?P<имя>[a-z_]+\.py)")


def вызовы_прогонов(каталог: Path) -> dict[str, set[str]]:
    """Скрипты `scripts/`, которые зовут прогоны: имя → в каких файлах."""
    найдено: dict[str, set[str]] = {}
    for путь in sorted(каталог.glob("*.yml")):
        for совпадение in _ВЫЗОВ.finditer(путь.read_text(encoding="utf-8")):
            найдено.setdefault(совпадение["имя"], set()).add(путь.name)
    return найдено


def импорты_предполётной() -> set[str]:
    """Модули, которые предполётная импортирует, — как имена скриптов."""
    дерево = ast.parse(Path(preflight.__file__).read_text(encoding="utf-8"))
    имена: set[str] = set()
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.Import):
            имена.update(f"{a.name}.py" for a in узел.names)
        elif isinstance(узел, ast.ImportFrom) and узел.module and not узел.level:
            имена.add(f"{узел.module}.py")
    return имена


def test_разбор_вызовов_узнаёт_свои_и_пропускает_чужие(tmp_path: Path) -> None:
    (tmp_path / "x.yml").write_text(
        "run: |\n"
        "  python scripts/свой.py\n"
        "  python scripts/pr_check.py --sha x\n"
        "  python3 scripts/release.py \\\n"
        "  python .rules-catalogue/scripts/link_trails.py\n",
        encoding="utf-8",
    )
    assert вызовы_прогонов(tmp_path) == {
        "pr_check.py": {"x.yml"},
        "release.py": {"x.yml"},
    }


def test_разбор_видит_вызовы_в_дереве() -> None:
    """Пустая выборка сделала бы главный тест зелёным на пустоте."""
    вызовы = вызовы_прогонов(WORKFLOWS)
    assert "preflight.py" in вызовы
    assert "pr_check.py" in вызовы


def test_каждый_скрипт_прогонов_прогоняется_или_назван() -> None:
    названо = set(preflight.ПРОВЕРИТ_ПЛОЩАДКА) | set(preflight.НЕ_ПРОВЕРКИ)
    покрыто = импорты_предполётной() | названо | {"preflight.py"}
    пропущено = {
        скрипт: sorted(где)
        for скрипт, где in вызовы_прогонов(WORKFLOWS).items()
        if скрипт not in покрыто
    }
    assert not пропущено, (
        "прогоны зовут скрипты, которых предполётная не прогоняет и не называет: "
        f"{пропущено}. Импортируйте проверку в scripts/preflight.py или внесите "
        "скрипт в ПРОВЕРИТ_ПЛОЩАДКА / НЕ_ПРОВЕРКИ с причиной"
    )


def test_реестр_не_называет_того_чего_прогоны_не_зовут() -> None:
    """Запись о снятом скрипте печаталась бы как отложенная проверка — ложь."""
    вызовы = вызовы_прогонов(WORKFLOWS)
    лишние = sorted(
        скрипт
        for скрипт in (*preflight.ПРОВЕРИТ_ПЛОЩАДКА, *preflight.НЕ_ПРОВЕРКИ)
        if скрипт not in вызовы
    )
    assert not лишние, f"в реестре границы, но ни один прогон не зовёт: {лишние}"


def test_реестры_не_пересекаются_и_причины_названы() -> None:
    площадка, не_проверки = preflight.ПРОВЕРИТ_ПЛОЩАДКА, preflight.НЕ_ПРОВЕРКИ
    assert not set(площадка) & set(не_проверки)
    причины = (*площадка.values(), *не_проверки.values())
    assert all(причина.strip() for причина in причины)


def test_отложенное_печатается_и_в_счёт_не_входит() -> None:
    итог = preflight.report(["а"], [], deferred=["pr_check.py — опрашивает площадку"])
    assert "  · проверит площадка: pr_check.py — опрашивает площадку" in итог
    assert "проверок 1" in итог
