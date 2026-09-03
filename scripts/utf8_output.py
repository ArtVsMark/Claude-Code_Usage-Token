"""Свои потоки говорят UTF-8, а не то, что решила локаль (#63).

Половина, симметричная гейту `subprocess_encoding`: там читатель обязан
назвать кодировку, здесь **писатель** обязан её поставить. Задать одну без
другой мало — согласуются они только обе разом, и это выяснилось отказом.

## Инцидент

Гейт переписи ссылок запускается из теста подпроцессом. Читателю дописали
`encoding="utf-8"` — и три windows-ячейки покраснели заново, но уже с другой
стороны:

```
UnicodeDecodeError: 'utf-8' codec can't decode byte 0x97   ← у читателя
UnicodeEncodeError: 'charmap' codec can't encode characters ← у писателя
```

Дочерний Python печатал русский текст в кодировке локали (cp1252), родитель
читал его как UTF-8. До правки обе стороны ошибались одинаково и сходились;
после — разошлись, и стало видно, что кодировку задавала локаль, а не мы.

## Почему это тише, чем кажется

У `sys.stdout` умолчание `errors="strict"`, и он падает громко. У `sys.stderr`
умолчание `errors="backslashreplace"`, и он **не падает**: сообщение об отказе
выходит как `\\u043f\\u0435\\u0440...` — формально работающий гейт, который
перестал называть отказавшее. Ровно то, ради чего гейт и заводился.

## Почему в инструменте, а не переменной окружения в CI

`PYTHONIOENCODING` в workflow сделал бы читаемым прогон в облаке и оставил бы
слепым окно, в котором работают руками. Слепая зона переехала бы, а не
закрылась.

## Гейт живёт здесь же

Требование и его проверка в одном файле намеренно: правило «скрипт ставит
UTF-8 первым делом» нечем соблюдать вниманием — после первой правки три
скрипта звали функцию, а семь нет, и разница ничем не отмечалась.

Проверяется **порядок**, а не только наличие. Вызов после первой печати
бесполезен ровно там, где нужен: `preflight` звал функцию в середине `main`, а
отказ «не принимаю аргументов» печатал до неё — то есть единственный отказ,
который эта команда выдаёт до всякой работы, уходил в кодировку локали. Нашёл
это гейт на собственном дереве, до того как был дописан.

## Предмет: скрипты и точки входа пакета

Два рода запускаемого, и признаки у них разные. Скрипт объявляет себя блоком
`if __name__ == "__main__"`. Точка входа пакета — строкой в `[project.scripts]`
файла `pyproject.toml`, и никакого блока у неё нет: её зовёт обёртка, которую
делает установщик. Искать один признак у обоих значило бы пропустить второй —
а именно у него вывод читает чужой человек, а не прогон.

## Почему функция живёт в двух местах, а не в одном

Свести копии нельзя ни в одну сторону: пакет не может импортировать `scripts/`
(его ставят там, где никаких `scripts/` нет), а `scripts/` не может зависеть от
пакета — четыре workflow (`changelog`, `merge-queue`, `pr-check`,
`pr-metadata`) запускают скрипты **без установки**. Общего дома у неё нет, и
попытка его завести сломает либо установку, либо четыре прогона.

Разойтись копиям не даёт этот гейт: имя у функции одно, и ищется оно по имени.
"""

from __future__ import annotations

import ast
import re
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

EXIT_FAILED = 1
EXIT_BROKEN = 2

#: Имя, которое обязан позвать каждый скрипт, печатающий по-русски.
ГЕЙТ_ФУНКЦИЯ = "force_utf8_output"

_NON_ASCII = re.compile(r"[^\x00-\x7f]")


def force_utf8_output() -> None:
    """Заставить `stdout` и `stderr` этого процесса говорить UTF-8.

    Зовётся первым делом в `main`: до первой печати, иначе часть вывода уже
    ушла в локали. Поток без `reconfigure` (подменённый в тесте, перенаправ-
    ленный) пропускается молча — это не отказ, а отсутствие предмета.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(encoding="utf-8", errors="replace")


# ── гейт ──────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class Находка:
    """Скрипт, чей вывод уйдёт в кодировку локали."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


@dataclass(frozen=True)
class Результат:
    """Находки и **охват**: сколько скриптов проверено, сколько не предмет.

    Второе число не отладочное: если предметов вдруг ноль, «чисто» означает
    «проверять было нечего», и различать это обязан вывод, а не читатель.
    """

    находки: list[Находка]
    examined: int
    skipped: int


def _есть_запуск(дерево: ast.Module) -> bool:
    """Есть ли у модуля блок `if __name__ == "__main__"`.

    Он и делает файл скриптом: библиотеку никто не запускает процессом, и
    требовать от неё кодировку потоков нечего — потоки не её.
    """
    for узел in дерево.body:
        if not isinstance(узел, ast.If):
            continue
        проверка = ast.dump(узел.test)
        if "__name__" in проверка and "__main__" in проверка:
            return True
    return False


def _тело_без_docstring(тело: Sequence[ast.stmt]) -> list[ast.stmt]:
    if (
        тело
        and isinstance(тело[0], ast.Expr)
        and isinstance(тело[0].value, ast.Constant)
        and isinstance(тело[0].value.value, str)
    ):
        return list(тело[1:])
    return list(тело)


def _зовёт(узел: ast.AST) -> bool:
    """Оператор ли это «позвать ГЕЙТ_ФУНКЦИЯ и выбросить результат»."""
    return (
        isinstance(узел, ast.Expr)
        and isinstance(узел.value, ast.Call)
        and isinstance(узел.value.func, ast.Name)
        and узел.value.func.id == ГЕЙТ_ФУНКЦИЯ
    )


def _найти_вызов(
    корень: ast.AST, *, кроме: frozenset[int] = frozenset()
) -> ast.Expr | None:
    """Первый вызов ГЕЙТ_ФУНКЦИЯ внутри поддерева; `кроме` исключает узлы по id."""
    for узел in ast.walk(корень):
        if isinstance(узел, ast.Expr) and _зовёт(узел) and id(узел) not in кроме:
            return узел
    return None


def check_text(text: str, path: str, *, запускается: bool = False) -> list[Находка]:
    """Проверить один исходник. Пустой список — либо чисто, либо не предмет.

    `запускается` говорит, что модуль запускают снаружи, даже если блока
    `__main__` в нём нет: так устроена точка входа пакета — обёртку делает
    установщик, а признака в самом файле не остаётся.
    """
    дерево = ast.parse(text, filename=path)
    if not (запускается or _есть_запуск(дерево)) or not _NON_ASCII.search(text):
        return []

    main = next(
        (
            узел
            for узел in дерево.body
            if isinstance(узел, ast.FunctionDef) and узел.name == "main"
        ),
        None,
    )
    if main is None:
        # Скрипт устроен иначе: требовать от него именно `main` — значит
        # навязывать форму, а гейт про кодировку, а не про структуру.
        # Достаточно, что вызов вообще есть.
        if _найти_вызов(дерево) is not None:
            return []
        return [
            Находка(
                path,
                1,
                f"скрипт печатает по-русски и не зовёт {ГЕЙТ_ФУНКЦИЯ}() нигде — "
                "вывод уйдёт в кодировку локали",
            )
        ]

    тело = _тело_без_docstring(main.body)
    if тело and _зовёт(тело[0]):
        return []

    где = _найти_вызов(main)
    if где is None:
        внутри_main = frozenset(id(у) for у in ast.walk(main))
        снаружи = _найти_вызов(дерево, кроме=внутри_main)
        if снаружи is not None:
            return [
                Находка(
                    path,
                    снаружи.lineno,
                    f"{ГЕЙТ_ФУНКЦИЯ}() зовётся вне main — значит только при "
                    "запуске файлом. Вызов main() из кода и из теста остаётся "
                    "без кодировки, и разницу между двумя способами запуска "
                    "видно не будет",
                )
            ]
        return [
            Находка(
                path,
                main.lineno,
                f"main не зовёт {ГЕЙТ_ФУНКЦИЯ}() — на windows-раннере вывод "
                "уйдёт в кодировку локали: stdout упадёт с UnicodeEncodeError, "
                "а stderr молча напечатает \\uXXXX вместо сообщения об отказе",
            )
        ]
    return [
        Находка(
            path,
            где.lineno,
            f"{ГЕЙТ_ФУНКЦИЯ}() зовётся не первым делом в main. Всё, что "
            "напечатано до него, уже ушло в кодировку локали — а до первой "
            "работы команда печатает как раз отказы, ради которых и заведена",
        )
    ]


def entry_points(root: Path) -> list[Path]:
    """Модули, объявленные точками входа в `[project.scripts]`.

    Объявление вида `имя = "пакет.модуль:функция"`; берётся часть до двоеточия
    и ищется на диске — сперва под `src/`, потом от корня, чтобы раскладка
    пакета не была вшита сюда вторым местом.

    Объявленный, но не найденный модуль пропускается: несуществующая точка
    входа — отдельная беда, и ловит её проверка упаковки, а не эта.
    """
    try:
        данные = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError):
        return []

    проект = данные.get("project")
    объявления = проект.get("scripts", {}) if isinstance(проект, dict) else {}
    пути: list[Path] = []
    for цель in объявления.values():
        модуль = str(цель).split(":", 1)[0]
        относительный = Path(*модуль.split(".")).with_suffix(".py")
        for кандидат in (root / "src" / относительный, root / относительный):
            if кандидат.is_file():
                пути.append(кандидат)
                break
    return пути


def script_files(root: Path, *, files: Sequence[Path] | None = None) -> list[Path]:
    """Исходники `scripts/`. Охват назван отдельной функцией, а не спрятан."""
    каталог = root / "scripts"
    if files is not None:
        return [путь for путь in files if путь.parent == каталог]
    return sorted(каталог.glob("*.py"))


def check_tree(root: Path, *, files: Sequence[Path] | None = None) -> Результат:
    """Пройти `scripts/` целиком.

    Пустой предмет — отказ только на **собственном** обходе: перечисление от
    вызывающего бывает законно пустым (подделочное дерево в тестах), и судить
    о нём гейт не вправе.
    """
    объявленные = {путь.resolve() for путь in entry_points(root)}
    исходники = list(script_files(root, files=files))
    уже = {путь.resolve() for путь in исходники}
    if files is None:
        исходники += [путь for путь in entry_points(root) if путь.resolve() not in уже]
    else:
        # Перечисление чужое: точки входа здесь ОТБИРАЮТСЯ из него, а не
        # добавляются к нему. Дополнить чужой список значило бы проверить то,
        # чего вызывающий не передавал, — и «пусто» перестало бы значить пусто.
        исходники += [
            путь
            for путь in files
            if путь.resolve() in объявленные and путь.resolve() not in уже
        ]

    if not исходники and files is None:
        return Результат(
            [
                Находка(
                    str(root / "scripts"),
                    0,
                    "ни скриптов, ни точек входа не найдено — гейт без предмета",
                )
            ],
            examined=0,
            skipped=0,
        )

    находки: list[Находка] = []
    проверено = 0
    не_предмет = 0
    for путь in исходники:
        try:
            текст = путь.read_text(encoding="utf-8")
            дерево = ast.parse(текст, filename=str(путь))
        except (OSError, UnicodeDecodeError, SyntaxError):
            не_предмет += 1
            continue
        запускается = путь.resolve() in объявленные
        if not (запускается or _есть_запуск(дерево)) or not _NON_ASCII.search(текст):
            не_предмет += 1
            continue
        проверено += 1
        находки.extend(
            check_text(
                текст, путь.relative_to(root).as_posix(), запускается=запускается
            )
        )
    return Результат(находки, examined=проверено, skipped=не_предмет)


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8_output()

    import argparse

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", default=".", help="корень дерева")
    аргументы = parser.parse_args(argv)

    результат = check_tree(Path(аргументы.root).resolve())
    for находка in результат.находки:
        print(f"::error::{находка}")

    if результат.находки:
        print(
            f"\nскриптов без UTF-8 на потоках: {len(результат.находки)} "
            f"(проверено {результат.examined}, не предмет {результат.skipped})",
            file=sys.stderr,
        )
        return EXIT_FAILED

    print(
        f"UTF-8 на потоках ставят все скрипты "
        f"(проверено {результат.examined}, не предмет {результат.skipped})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
