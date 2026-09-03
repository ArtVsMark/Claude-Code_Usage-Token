"""Текстовый режим подпроцесса обязан называть кодировку (#63).

`subprocess.run(..., text=True)` без `encoding=` берёт **кодировку локали**. На
ubuntu и macos это UTF-8, на windows-раннере — cp1252, где байт `0x81` не
определён вовсе. Внутренний язык проекта русский: git отдаёт русские темы
коммитов, а кириллица в именах файлов — обычное дело, не экзотика.

## Инцидент

Гейты `windows-latest · python 3.11` и `3.13` покраснели на #62:

```
UnicodeDecodeError: 'charmap' codec can't decode byte 0x81 in position 11
AttributeError: 'NoneType' object has no attribute 'strip'
```

Второе сообщение видно первым, и причину по нему искать негде: декодирование
падает **в своей нити**, `stdout` остаётся `None`, и падает уже вызывающий.

Байт `0x81` — вторая половина буквы **«с»** в UTF-8 (`D1 81`). Отсюда два
свойства, делающие промах живучим:

* виден **только на трети матрицы** — три ячейки из девяти;
* и **только если в выводе попалась нужная буква**. На `замер` не падает, на
  `список` падает. Тест может годами быть зелёным на неверном коде.

## Почему гейт, а не внимательность

Прецедент в проекте уже был: `preflight` и `storage` задают кодировку явно —
и всё равно два новых вызова написаны без неё, дважды подряд, разными
заходами. Правило, которое соблюдается вниманием, не соблюдается.

## Что считается текстовым режимом

Шире буквы задачи, и это не расширение из аккуратности, а семантика CPython:
текстовый режим включает **любой** из `text`, `universal_newlines`, `errors`.
То есть `errors="replace"` без `encoding=` даёт ровно ту же локаль, что и
`text=True`, — и заметить это ещё труднее, потому что `errors` выглядит
предусмотрительностью.

## Где гейт воздерживается — и почему именно так

**`**kwargs` в вызове.** Кодировка может прийти оттуда, и заглянуть внутрь
нечем: разбор статический, без прогона. Отказ здесь был бы догадкой.

**Неконстантное значение `text=`.** А вот тут гейт краснеет, хотя значение ему
неизвестно. Цена ошибки несимметрична: ложный отказ стоит одного дописанного
`encoding="utf-8"`, который безвреден при любом значении флага, а пропуск
стоит слепоты на трети матрицы, проявляющейся через месяцы и не на той букве.

## Граница

Проверяются **вызовы, похожие на subprocess** — по имени функции, а не по
разрешённому импорту. Отслеживать псевдонимы модуля разбор без прогона не
может, а список имён (`run`, `Popen`, `check_output`, `check_call`, `call`) в
этом дереве однозначен. Совпадение имени у чужой функции даст ложный отказ —
и он дешевле пропуска, по тому же счёту, что выше.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from utf8_output import force_utf8_output

EXIT_FAILED = 1
EXIT_BROKEN = 2

#: Функции subprocess, принимающие кодировку. `getoutput` и `getstatusoutput`
#: сюда не входят: они этих аргументов не принимают вовсе.
ФУНКЦИИ = frozenset({"run", "Popen", "check_output", "check_call", "call"})

#: Аргументы, включающие текстовый режим. `errors` здесь не по аналогии, а по
#: документации: «If encoding or errors are specified, or text is true, the
#: file objects are opened in text mode».
ТРИГГЕРЫ = frozenset({"text", "universal_newlines", "errors"})


@dataclass(frozen=True)
class Находка:
    """Вызов, открывающий текстовый режим и не называющий кодировку."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


@dataclass(frozen=True)
class Результат:
    """Находки и **охват**: сколько файлов разобрано, сколько пропущено.

    Охват — часть вывода, а не отладочная информация: «чисто» без числа
    разобранных файлов неотличимо от «ничего не проверяли».
    """

    находки: list[Находка]
    examined: int
    skipped: int


def _триггер(kw: ast.keyword) -> str | None:
    """Имя аргумента, открывающего текстовый режим, — или `None`.

    Явные `text=False` и `errors=None` не открывают его: это байтовый режим,
    и кодировка ему не нужна. Неконстантное значение считается включающим —
    см. «Где гейт воздерживается» в описании модуля.
    """
    if kw.arg is None or kw.arg not in ТРИГГЕРЫ:
        return None
    if isinstance(kw.value, ast.Constant) and not kw.value.value:
        return None
    return kw.arg


def _кодировка_названа(keywords: Sequence[ast.keyword]) -> bool:
    """Задана ли кодировка явно. `encoding=None` — это та же локаль."""
    for kw in keywords:
        if kw.arg != "encoding":
            continue
        return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
    return False


def _имя_функции(node: ast.Call) -> str | None:
    """Последнее звено имени вызываемого: `subprocess.run` → `run`."""
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    if isinstance(node.func, ast.Name):
        return node.func.id
    return None


def импорты_subprocess(дерево: ast.AST) -> tuple[frozenset[str], dict[str, str]]:
    """Чем в ЭТОМ файле зовётся `subprocess`: модуль и имена, взятые из него.

    Возвращает (имена модуля, отображение «местное имя → исходное»).
    `import subprocess as sp` даёт модулю имя `sp`; `from subprocess import run
    as запустить` кладёт `{"запустить": "run"}` — псевдоним обязан вести к
    исходному имени, иначе он прячет функцию от списка проверяемых.

    Без этого разбора гейт судит по последнему звену — и `pr_check.run(...)`,
    своя функция с тем же именем, становится ложной находкой. У проверки
    кодировки это не проявлялось по счастливой случайности: у своих функций
    нет `text=`, то есть не срабатывал триггер. У проверки дедлайна триггера
    нет, и случайность кончилась (#95).
    """
    модули: set[str] = set()
    функции: dict[str, str] = {}
    for узел in ast.walk(дерево):
        if isinstance(узел, ast.Import):
            for имя in узел.names:
                if имя.name == "subprocess":
                    модули.add(имя.asname or имя.name)
        elif isinstance(узел, ast.ImportFrom) and узел.module == "subprocess":
            for имя in узел.names:
                функции[имя.asname or имя.name] = имя.name
    return frozenset(модули), функции


def из_subprocess(
    node: ast.Call, *, модули: frozenset[str], функции: dict[str, str]
) -> str | None:
    """ИСХОДНОЕ имя функции `subprocess` в этом вызове — или `None`.

    `subprocess.run(…)` → `"run"`, если модуль импортирован под этим именем.
    `запустить(…)` → `"run"`, если имя взято `from subprocess import run as …`.
    `pr_check.run(…)` → `None`: `pr_check` не имя модуля subprocess.

    Возвращается имя, а не «да/нет», потому что список проверяемых функций у
    каждого гейта свой, а сверять его надо с исходным именем: псевдоним иначе
    прячет функцию, и гейт молчит там, где обязан говорить.
    """
    if isinstance(node.func, ast.Attribute):
        владелец = node.func.value
        if isinstance(владелец, ast.Name) and владелец.id in модули:
            return node.func.attr
        return None
    if isinstance(node.func, ast.Name):
        return функции.get(node.func.id)
    return None


def check_text(text: str, path: str) -> list[Находка]:
    """Найти вызовы без кодировки в одном исходнике.

    Синтаксическая ошибка — не находка: такой файл завалит и тесты, и `ruff`,
    и сказать о нём здесь нечего, кроме того, что разобрать его не вышло.
    """
    дерево = ast.parse(text, filename=path)
    находки: list[Находка] = []
    for узел in ast.walk(дерево):
        if not isinstance(узел, ast.Call):
            continue
        имя = _имя_функции(узел)
        if имя not in ФУНКЦИИ:
            continue
        if any(kw.arg is None for kw in узел.keywords):
            # `**kwargs`: кодировка может прийти оттуда, и заглянуть нечем.
            continue
        триггеры = sorted(
            имя for kw in узел.keywords if (имя := _триггер(kw)) is not None
        )
        if not триггеры or _кодировка_названа(узел.keywords):
            continue
        перечень = ", ".join(f"`{т}`" for т in триггеры)
        находки.append(
            Находка(
                path,
                узел.lineno,
                f"{имя}(…) открывает текстовый режим ({перечень}), но не "
                'называет кодировку. Без `encoding="utf-8"` берётся кодировка '
                "локали: на windows-раннере это cp1252, и первая же русская "
                "буква в выводе роняет чтение в своей нити — вызывающий видит "
                "лишь `stdout=None`",
            )
        )
    return находки


def python_files(root: Path, *, files: Sequence[Path] | None = None) -> list[Path]:
    """Исходники, которые надо разобрать. Отдельной функцией — чтобы охват был назван.

    Перечисление даёт вызывающий, когда оно у него уже есть: в `preflight` оно
    одно на все проверки, и второй поход в git разошёлся бы с первым.
    """
    if files is not None:
        return [путь for путь in files if путь.suffix == ".py"]

    ответ = subprocess.run(
        ["git", "-C", str(root), "ls-files", "-z"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
        timeout=30,
    )
    return [root / имя for имя in ответ.stdout.split("\0") if имя.endswith(".py")]


def check_tree(root: Path, *, files: Sequence[Path] | None = None) -> Результат:
    """Пройти дерево целиком.

    «Предмета не найдено» — отказ, но только на **собственном** перечислении:
    пустой обход означает, что git ничего не отдал, и молчать об этом нельзя.
    Когда перечисление даёт вызывающий, судить о нём гейт не вправе: пустой
    список там — законное состояние подделочного дерева, а не поломка. Ответом
    в этом случае остаётся охват, и его печатает вызывающий.
    """
    исходники = python_files(root, files=files)
    if not исходники and files is None:
        return Результат(
            [Находка(str(root), 0, "исходников .py не найдено — гейт без предмета")],
            examined=0,
            skipped=0,
        )

    находки: list[Находка] = []
    разобрано = 0
    пропущено = 0
    for путь in исходники:
        try:
            текст = путь.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            пропущено += 1
            continue
        относительный = путь.relative_to(root).as_posix()
        try:
            находки.extend(check_text(текст, относительный))
        except SyntaxError:
            пропущено += 1
            continue
        разобрано += 1
    return Результат(находки, examined=разобрано, skipped=пропущено)


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8_output()

    import argparse

    parser = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    parser.add_argument("--root", default=".", help="корень дерева")
    аргументы = parser.parse_args(argv)
    root = Path(аргументы.root).resolve()

    try:
        результат = check_tree(root)
    except (subprocess.CalledProcessError, OSError) as exc:
        # Отдельный код: «прогнать не вышло» — не то же, что «найден вызов без
        # кодировки», и путать их нельзя. Без этого перечисление, упавшее вне
        # git-репозитория, давало трейсбек и код 1 — ровно тот же, что у
        # настоящей находки.
        print(f"перечислить исходники не вышло: {exc}", file=sys.stderr)
        return EXIT_BROKEN

    for находка in результат.находки:
        print(f"::error::{находка}")

    if результат.находки:
        print(
            f"\nвызовов без кодировки: {len(результат.находки)} "
            f"(разобрано файлов {результат.examined}, "
            f"пропущено {результат.skipped})",
            file=sys.stderr,
        )
        return EXIT_FAILED

    print(
        f"кодировка подпроцессов названа везде "
        f"(разобрано файлов {результат.examined}, пропущено {результат.skipped})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
