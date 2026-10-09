"""Код написан на версии планки, а не только объявлен на ней (правило 217, #154).

Планка `requires-python` — два обещания. Первое, «пакет заработает на этой
версии», держат матрица проверок и подъём #127. Второе, «код пишется на этой
версии», не держал никто: после переезда на 3.14 `from __future__ import
annotations` стоял в 77 файлах из 80, а хуки окна жили исключением
`per-file-target-version = py311`. Номер переехал, стиль остался прежним.

Приём — `scripts/check_py_style.py` каталога правил, в нашей форме: проверка
дерева, которую зовёт `preflight`.

ЦЕЛЬ ВЫВОДИТСЯ ИЗ ПЛАНКИ, А НЕ ВПИСЫВАЕТСЯ (005). Каждое требование знает
версию, с которой оно доступно, и действует, только когда планка до неё
доросла. Цель ruff в `pyproject.toml` сверяется с планкой, а исключение по
файлам — отказ: исключений нет, и это решение, а не недосмотр.

ИСКЛЮЧЕНИЙ НЕТ ПОТОМУ, ЧТО ПРИЧИНА УБРАНА ТАМ, ГДЕ ЖИЛА. Хуки окна держались на
3.11, потому что их звал системный `python3`. Теперь их зовёт интерпретатор
планки (`.claude/hooks/push_guard.sh`, шаг 1 #154), и держать их ниже незачем.

Планку здесь разбирает `планка()` — единственный разбор на Python. Хукам она
нужна без Python, и у них свой разбор `.claude/hooks/floor.sh`; что оба
отвечают одно, держит `tests/test_push_guard_wrapper.py`.
"""

import io
import re
import tokenize
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

#: С какой версии доступно то, что гейт требует.
ЛЕНИВЫЕ_АННОТАЦИИ = (3, 14)  # PEP 649/749: __future__ annotations лишний
EXCEPT_БЕЗ_СКОБОК = (3, 14)  # PEP 758: except A, B: без скобок

_FUTURE = re.compile(r"^from __future__ import annotations\s*$", re.MULTILINE)
_ПЛАНКА = re.compile(r"^\s*>=\s*(\d+)\.(\d+)")


@dataclass(frozen=True)
class Находка:
    """Место, где код отстаёт от планки."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


@dataclass(frozen=True)
class Итог:
    """Находки и счёт: сколько файлов просмотрено и на какой планке."""

    находки: list[Находка]
    планка: tuple[int, int]
    файлов: int


def планка(root: Path) -> tuple[int, int]:
    """Планка `requires-python` как (мажор, минор). Отказ — ValueError."""
    with (root / "pyproject.toml").open("rb") as fh:
        требование = tomllib.load(fh)["project"]["requires-python"]
    совпадение = _ПЛАНКА.match(требование)
    if совпадение is None:
        raise ValueError(f"планка не вида >=X.Y: {требование!r}")
    return int(совпадение[1]), int(совпадение[2])


def except_в_скобках(text: str) -> list[int]:
    """Строки, где несколько исключений взяты в скобки без `as`.

    Токенами, а не регулярным выражением: `except (` внутри строки или
    комментария — не предмет. `except*` — предмет тот же. Кортеж из одного
    элемента `except (A,):` — не предмет: без скобок он не пишется вовсе.
    """
    открывают, закрывают = {"(", "[", "{"}, {")", "]", "}"}
    токены = [
        т
        for т in tokenize.generate_tokens(io.StringIO(text).readline)
        if т.type not in (tokenize.NL, tokenize.COMMENT)
    ]
    строки: list[int] = []
    for i, т in enumerate(токены):
        if т.type != tokenize.NAME or т.string != "except":
            continue
        k = i + 1
        if k < len(токены) and токены[k].string == "*":
            k += 1
        if k >= len(токены) or токены[k].string != "(":
            continue
        глубина, элементов, j = 0, 1, k
        while j < len(токены):
            s = токены[j].string
            if s in открывают:
                глубина += 1
            elif s in закрывают:
                глубина -= 1
            elif (
                глубина == 1
                and s == ","
                and j + 1 < len(токены)
                and токены[j + 1].string != ")"
            ):
                элементов += 1
            if глубина == 0:
                break
            j += 1
        if элементов > 1 and j + 1 < len(токены) and токены[j + 1].string == ":":
            строки.append(т.start[0])
    return строки


def check_text(text: str, path: str, планка_: tuple[int, int]) -> list[Находка]:
    """Что в одном файле отстаёт от планки."""
    версия = f"{планка_[0]}.{планка_[1]}"
    находки: list[Находка] = []
    if планка_ >= ЛЕНИВЫЕ_АННОТАЦИИ:
        for совпадение in _FUTURE.finditer(text):
            строка = text.count("\n", 0, совпадение.start()) + 1
            находки.append(
                Находка(
                    path,
                    строка,
                    f"`from __future__ import annotations` при планке {версия} — "
                    "аннотации и так ленивые (PEP 649)",
                )
            )
    if планка_ >= EXCEPT_БЕЗ_СКОБОК:
        находки += [
            Находка(
                path,
                n,
                f"`except (A, B):` без `as` при планке {версия} — "
                "скобки не нужны (PEP 758)",
            )
            for n in except_в_скобках(text)
        ]
    return находки


def check_ruff(root: Path, планка_: tuple[int, int]) -> list[Находка]:
    """Цель ruff выводится из планки, а исключений по файлам нет (005)."""
    with (root / "pyproject.toml").open("rb") as fh:
        ruff = tomllib.load(fh).get("tool", {}).get("ruff", {})
    ждём = f"py{планка_[0]}{планка_[1]}"
    находки: list[Находка] = []
    if ruff.get("target-version") != ждём:
        находки.append(
            Находка(
                "pyproject.toml",
                0,
                f"цель ruff {ruff.get('target-version')!r}, а планка требует {ждём!r}",
            )
        )
    for образец, цель in (ruff.get("per-file-target-version") or {}).items():
        находки.append(
            Находка(
                "pyproject.toml",
                0,
                f"исключение по файлам: {образец} = {цель}. Исключений нет — "
                "файл ниже планки значит, что его зовёт не тот интерпретатор",
            )
        )
    return находки


def check_tree(root: Path, *, files: Sequence[Path]) -> Итог:
    """Пройти отслеживаемые .py и манифест. `files` даёт вызывающий."""
    планка_ = планка(root)
    исходники = [ф for ф in files if ф.suffix == ".py"]
    находки = check_ruff(root, планка_)
    for файл in исходники:
        путь = файл.relative_to(root).as_posix()
        находки += check_text(файл.read_text(encoding="utf-8"), путь, планка_)
    return Итог(находки, планка_, len(исходники))
