"""Группа отмены прогона включает голову, а не только номер PR (#88).

Отмена предыдущего прогона по `concurrency` экономит минуты и почти всегда
безобидна. Почти — потому что она решает, какой из двух прогонов **лишний**, а
решает по имени группы. Если в имени только номер PR, то лишним объявляется
прогон на другом коммите — и вытеснить он может более новый.

## Инцидент

PR #87, 2026-09-03. За 35 секунд пришло четыре события: `opened`, два
`labeled` и `synchronize` от очереди мержей. Пять прогонов обязательной
проверки, группа — `pr-check-87`. Порядок доставки событий не совпал с
порядком коммитов: `labeled` встал в очередь до `synchronize`, а доставлен
после, и принёс с собой голову, которая к тому моменту устарела.

Выжил последний, и он считал **старый** коммит. Итог:

* на актуальной голове обязательной проверки нет вовсе — ruleset ждёт имени
  `PR check`, а создать его больше некому: событий не осталось;
* на устаревшей висит красное, и оно правдоподобно — «ci.yml=cancelled».

Из такого состояния автоматика не выходит: новый прогон рождается только от
нового события, а метки проставлены, пушей нет, ветку очередь уже подтянула.
Расклинивается вручную, перезапуском отменённых прогонов.

## Почему гейт, а не внимательность

Ни один из двух механизмов не сломан по отдельности. `head.sha` из полезной
нагрузки события — единственное, что там есть; отмена по номеру PR — ровно то,
что написано во всех примерах площадки. Сломано их сочетание, и увидеть его
можно только в момент гонки, то есть никогда — при чтении диффа.

## Что проверяется

Workflow, который слушает `pull_request` и **отменяет** предыдущий прогон,
обязан включить голову в имя группы. Признак головы — подстрока `head.sha`.

Три случая не проверяются, и каждый законен:

* блока `concurrency` нет вовсе — отменять нечего;
* `cancel-in-progress` не задан — умолчание площадки `false`;
* `cancel-in-progress: false` сказан вслух.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from utf8_output import force_utf8_output

EXIT_FAILED = 1

#: Признак головы в выражении группы. Подстрокой, а не полным выражением:
#: `github.event.pull_request.head.sha` и `github.event.pull_request.head.sha
#: || github.sha` — оба верные, и перечислять их формы значило бы краснеть на
#: следующей.
ГОЛОВА = "head.sha"

#: Строка верхнего уровня: ключ без отступа. Ею кончается тело любого блока.
_ВЕРХНИЙ = re.compile(r"^\S")

_GROUP = re.compile(r"^\s+group\s*:\s*(?P<значение>\S.*?)\s*$")
_CANCEL = re.compile(r"^\s+cancel-in-progress\s*:\s*(?P<значение>\S.*?)\s*$")
_PR_EVENT = re.compile(r"^\s+pull_request(?:_target)?\s*:")


@dataclass(frozen=True)
class Finding:
    """Одна находка: файл, строка и что именно не так."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


def _тело(строки: Sequence[str], ключ: str) -> list[tuple[int, str]]:
    """Строки блока верхнего уровня ``ключ:`` — до следующего ключа без отступа.

    Ключ может быть в кавычках: YAML 1.1 читает голое ``on`` как булево, и
    осторожные авторы пишут ``"on":``. Обе формы — один и тот же блок.
    """
    начало = re.compile(
        rf"""^(?:{re.escape(ключ)}|"{re.escape(ключ)}"|'{re.escape(ключ)}')\s*:"""
    )
    первая = None
    for номер, строка in enumerate(строки):
        if начало.match(строка):
            первая = номер
            break
    if первая is None:
        return []

    тело: list[tuple[int, str]] = []
    for номер in range(первая + 1, len(строки)):
        строка = строки[номер]
        if строка.strip() and _ВЕРХНИЙ.match(строка):
            break
        тело.append((номер, строка))
    return тело


def listens_to_pr(text: str) -> bool:
    """Слушает ли workflow события pull request."""
    строки = text.splitlines()
    for _, строка in _тело(строки, "on"):
        if _PR_EVENT.match(строка):
            return True
    # Однострочная форма: `on: [pull_request]` или `on: pull_request`.
    for строка in строки:
        if re.match(r"^(?:on|\"on\"|'on')\s*:\s*\S", строка):
            return "pull_request" in строка
    return False


def check_text(text: str, path: str) -> list[Finding]:
    """Проверить один workflow."""
    if not listens_to_pr(text):
        return []

    строки = text.splitlines()
    тело = _тело(строки, "concurrency")
    if not тело:
        # Отмены нет — вытеснять нечем.
        return []

    группа: str | None = None
    строка_группы = 0
    отмена: str | None = None
    for номер, строка in тело:
        совпадение = _GROUP.match(строка)
        if совпадение is not None:
            группа = совпадение.group("значение")
            строка_группы = номер + 1
        совпадение = _CANCEL.match(строка)
        if совпадение is not None:
            отмена = совпадение.group("значение")

    if отмена is None or отмена.strip().lower() in {"false", "'false'", '"false"'}:
        # Умолчание площадки — не отменять; сказанное вслух `false` тем более.
        return []

    if группа is None:
        return [
            Finding(
                path,
                тело[0][0] + 1,
                "cancel-in-progress задан, а group — нет: площадка возьмёт "
                "группу по умолчанию, и какой прогон окажется лишним, "
                "предсказать нельзя",
            )
        ]

    if ГОЛОВА in группа:
        return []

    return [
        Finding(
            path,
            строка_группы,
            f"группа отмены не называет голову ({ГОЛОВА!r}): "
            f"«{группа}». Прогоны на РАЗНЫХ коммитах попадут в одну группу, и "
            "вытеснить может более новый — события приходят не в том порядке, "
            "в каком сделаны коммиты. Тогда последнее слово останется за "
            "прогоном на устаревшей голове, а на актуальной проверки не будет "
            "вовсе, и создать её будет уже нечем",
        )
    ]


def workflow_files(root: Path) -> list[Path]:
    """Файлы workflow проекта. Отдельной функцией — чтобы охват был назван."""
    каталог = root / ".github" / "workflows"
    return sorted(каталог.glob("*.yml")) + sorted(каталог.glob("*.yaml"))


def check_workflows(root: Path) -> list[Finding]:
    """Пройти по всем workflow проекта.

    «Ни одного workflow с `pull_request`» — не «чисто», а «предмета нет».
    Утверждение о действительности, и оно устаревает молча: переезд каталога
    или переход на другое событие выключил бы гейт, оставив его зелёным.
    """
    файлы = workflow_files(root)
    находки: list[Finding] = []
    предмет = 0
    for путь in файлы:
        текст = путь.read_text(encoding="utf-8")
        if listens_to_pr(текст):
            предмет += 1
        находки += check_text(текст, str(путь.relative_to(root)))

    if предмет == 0:
        каталог = root / ".github" / "workflows"
        return [
            Finding(
                str(каталог),
                0,
                "ни одного workflow на событиях pull request — гейт остался "
                "без предмета проверки",
            )
        ]
    return находки


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8_output()

    корень = Path(argv[0]) if argv else Path(__file__).resolve().parent.parent
    находки = check_workflows(корень)
    for находка in находки:
        print(f"::error::{находка}")
    if находки:
        print(f"\nгрупп отмены без головы: {len(находки)}", file=sys.stderr)
        return EXIT_FAILED

    предмет = sum(
        1 for путь in workflow_files(корень) if listens_to_pr(путь.read_text("utf-8"))
    )
    print(f"группы отмены называют голову (workflow на pull request {предмет})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
