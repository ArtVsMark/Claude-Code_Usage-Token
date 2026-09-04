"""Сборка значков витрины (#160 каталога).

Значки живут на отдельной ветке `badges`, а не в `main`. Причина перенесена
из каталога вместе с его инцидентом: значок в общей ветке пересобирается от
КАЖДОГО изменения, и это давало конфликт на каждом слиянии и красное не
потому, что что-то сломалось, а потому что число сдвинулось. Проверка, которая
краснеет на верной работе, — ровно та, которую приучаются пропускать
(правило 051).

## Что здесь считается, а что нет

Ничего своего. Значение каждого значка даёт `preflight.expected_badge` — тот
же код, которым гейт витрины сверял значок, пока тот лежал в `main`. Вторая
механика на ту же территорию разошлась бы с первой молча (правило 022), и
разошлась бы именно в ту сторону, где никто не смотрит: значок собран одним
правилом, проверен другим.

## Состав берётся из витрины, а не из списка здесь

`.rules/showcase.json` уже перечисляет, у какого вопроса есть значок и где он
лежит. Второй список значков рядом с первым — те же две классификации одной
территории; расходятся они молча и обнаруживаются брошенным файлом, который
отвечает, не имея производителя.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

import preflight
from utf8_output import force_utf8_output

#: Собрать не вышло: витрина не прочитана, правила вывода нет, диск отказал.
EXIT_BROKEN = 2


def declared(root: Path) -> list[tuple[str, str]]:
    """Пары «вопрос — путь значка» из набора витрины, в порядке набора."""
    набор = json.loads((root / preflight.SHOWCASE_SET).read_text(encoding="utf-8"))[
        "questions"
    ]
    пары: list[tuple[str, str]] = []
    for вопрос in набор:
        значок = вопрос.get("badge")
        if isinstance(значок, str) and значок:
            пары.append((str(вопрос.get("id")), значок))
    return пары


def build(root: Path, *, out: Path | None = None) -> list[Path]:
    """Собрать все объявленные значки. Возвращает записанные файлы.

    Отсутствие правила вывода — **отказ**, а не пропуск: значок, который
    некому посчитать, окажется на ветке пустым или застывшим, а витрина будет
    отвечать им как живым числом.
    """
    записано: list[Path] = []
    for qid, относительный in declared(root):
        значение = preflight.expected_badge(qid, root)
        if значение is None:
            raise ValueError(
                f"{qid}: значок {относительный} объявлен витриной, а правила "
                "вывода для него нет. Собрать его нечем, и пустой файл на "
                "ветке отвечал бы вместо живого числа"
            )
        путь = (out or root) / относительный
        путь.parent.mkdir(parents=True, exist_ok=True)
        путь.write_text(
            json.dumps(значение, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        записано.append(путь)
    return записано


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8_output()

    аргументы = list(sys.argv[1:] if argv is None else argv)
    корень = Path(аргументы[0]) if аргументы else preflight.ROOT
    try:
        файлы = build(корень)
    except (OSError, ValueError, KeyError, TypeError) as отказ:
        print(f"::error::значки не собраны: {отказ}", file=sys.stderr)
        return EXIT_BROKEN

    for путь in файлы:
        print(f"собран {путь.relative_to(корень)}")
    print(f"значков собрано {len(файлы)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
