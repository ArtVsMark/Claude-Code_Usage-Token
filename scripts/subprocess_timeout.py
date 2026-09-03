"""У вызова подпроцесса есть дедлайн (#95).

Ограничение времени на джоб не покрывает **вызов**. `timeout-minutes` у
прогона — это 5–15 минут на всё, а зависнуть можно на одной команде и до
первой её строки; локально у человека нет и этого.

## Замер, ради которого гейт заведён

2026-09-03: одиннадцать вызовов `subprocess` в `scripts/` и `src/`, **ни
одного** с `timeout=`. Дыра не теоретическая: `git push` в чужой приватный
репозиторий при истёкшем ключе ждёт ввода с терминала, которого в прогоне нет,
— и ждёт до таймаута джоба.

## Почему требуется НАЗВАННОЕ значение, а не одинаковое

Дедлайн выводится из того, что вызов делает: секунды локальному `git`, минуты —
прогону всего набора тестов. Одинаковое число здесь стало бы либо ложным
отказом на длинном вызове, либо бесполезным на коротком. Поэтому гейт требует,
чтобы значение было **названо**, и не судит, какое оно.

## Где гейт воздерживается, и почему это не поблажка

**`Popen`.** У конструктора параметра `timeout` нет вовсе — он есть у `wait` и
`communicate`. Требовать его от `Popen` значило бы требовать невозможного, а
проверять парный вызов ниже по коду гейт не умеет: между ними может стоять что
угодно. Такие места остаются на чтении.

**`**kwargs`.** Дедлайн может прийти оттуда, и заглянуть нечем.

**Чужой `run`.** Предмет определяется не по последнему звену имени, а по тому,
как `subprocess` назван в ЭТОМ файле: `pr_check.run(…)` и `merge_queue.run(…)`
— свои функции, и первая редакция гейта объявила их находками. «Починка» тогда
дописала бы им несуществующий параметр и сломала пятнадцать тестов — что она и
сделала, прежде чем это заметили.

`timeout=None` при этом воздержанием НЕ считается: это явно объявленное
отсутствие дедлайна, то есть ровно то, что гейт ищет.

## Общий обходчик — один

Разбор AST, список функций `subprocess` и перечисление исходников живут в
`subprocess_encoding.py` и берутся отсюда импортом. Вторая копия обхода
разошлась бы с первой при первой же правке, а два гейта по одному дереву — это
не повод заводить два обходчика.
"""

from __future__ import annotations

import ast
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

from subprocess_encoding import (
    EXIT_BROKEN,
    EXIT_FAILED,
    Находка,
    Результат,
    python_files,
    из_subprocess,
    импорты_subprocess,
)
from utf8_output import force_utf8_output

#: Функции `subprocess`, у которых параметр `timeout` вообще существует.
#: `Popen` сюда не входит намеренно — см. «Где гейт воздерживается».
ФУНКЦИИ_С_ДЕДЛАЙНОМ = frozenset({"run", "call", "check_call", "check_output"})


def _дедлайн_назван(keywords: Sequence[ast.keyword]) -> bool:
    """Задан ли `timeout` явным значением. `timeout=None` — это «без дедлайна»."""
    for kw in keywords:
        if kw.arg != "timeout":
            continue
        return not (isinstance(kw.value, ast.Constant) and kw.value.value is None)
    return False


def check_text(text: str, path: str) -> list[Находка]:
    """Найти вызовы без дедлайна в одном исходнике."""
    дерево = ast.parse(text, filename=path)
    модули, функции = импорты_subprocess(дерево)
    находки: list[Находка] = []
    for узел in ast.walk(дерево):
        if not isinstance(узел, ast.Call):
            continue
        # Имя берётся ИСХОДНОЕ, а не последнее звено вызова: своя функция с
        # именем `run` (`pr_check.run(…)`, `merge_queue.run(…)`) предметом не
        # является, а импорт под псевдонимом — является.
        имя = из_subprocess(узел, модули=модули, функции=функции)
        if имя not in ФУНКЦИИ_С_ДЕДЛАЙНОМ:
            continue
        if any(kw.arg is None for kw in узел.keywords):
            continue
        if _дедлайн_назван(узел.keywords):
            continue
        находки.append(
            Находка(
                path,
                узел.lineno,
                f"{имя}(…) без `timeout=`. Зависший вызов будет ждать до "
                "таймаута ДЖОБА — минуты вместо секунд, — а локально не "
                "остановится вовсе. Значение выводится из того, что делает "
                "вызов, и одинаковым быть не обязано: важно, чтобы оно было "
                "названо",
            )
        )
    return находки


def check_tree(root: Path, *, files: Sequence[Path] | None = None) -> Результат:
    """Пройти дерево целиком.

    «Предмета не найдено» — отказ, но только на **собственном** перечислении:
    когда список файлов передан снаружи, пустота означает «в этом изменении
    нет исходников», а не «гейт остался без предмета».
    """
    свои = files is None
    исходники = python_files(root, files=files)
    if not исходники and свои:
        return Результат(
            [Находка(str(root), 0, "исходников не найдено — гейт без предмета")],
            examined=0,
            skipped=0,
        )

    находки: list[Находка] = []
    разобрано = 0
    пропущено = 0
    for путь in исходники:
        try:
            текст = путь.read_text(encoding="utf-8")
        except OSError:
            пропущено += 1
            continue
        try:
            находки += check_text(текст, str(путь.relative_to(root)))
        except SyntaxError:
            пропущено += 1
            continue
        разобрано += 1
    return Результат(находки, examined=разобрано, skipped=пропущено)


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8_output()

    корень = Path(argv[0]) if argv else Path(__file__).resolve().parent.parent
    try:
        итог = check_tree(корень)
    except (subprocess.CalledProcessError, OSError) as отказ:
        print(f"::error::перечислить исходники не вышло: {отказ}", file=sys.stderr)
        return EXIT_BROKEN

    for находка in итог.находки:
        print(f"::error::{находка}")
    if итог.находки:
        print(f"\nвызовов без дедлайна: {len(итог.находки)}", file=sys.stderr)
        return EXIT_FAILED
    print(f"дедлайн назван у всех вызовов (исходников {итог.examined})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
