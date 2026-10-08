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

import workflow_on
from utf8_output import force_utf8_output

EXIT_FAILED = 1

#: Признак головы в выражении группы. Подстрокой, а не полным выражением:
#: `github.event.pull_request.head.sha` и `github.event.pull_request.head.sha
#: || github.sha` — оба верные, и перечислять их формы значило бы краснеть на
#: следующей.
ГОЛОВА = "head.sha"

#: Строка блока `concurrency:` на любом уровне: верхнем или `jobs.<id>` (#151).
_БЛОК = re.compile(r"^(?P<отступ>\s*)concurrency\s*:\s*(?:#.*)?$")

#: Выражение площадки `${{ … }}`. Голова засчитывается только внутри него:
#: подстрока в комментарии `# head.sha` голову в группу не добавляет (#151).
_ВЫРАЖЕНИЕ = re.compile(r"\$\{\{(.*?)\}\}")

_GROUP = re.compile(r"^\s+group\s*:\s*(?P<значение>\S.*?)\s*$")
_CANCEL = re.compile(r"^\s+cancel-in-progress\s*:\s*(?P<значение>\S.*?)\s*$")


@dataclass(frozen=True)
class Finding:
    """Одна находка: файл, строка и что именно не так."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


#: События, у которых гонка групп одна и та же: оба несут голову PR.
СОБЫТИЯ_PR = frozenset({"pull_request", "pull_request_target"})


def listens_to_pr(text: str) -> bool:
    """Слушает ли workflow события pull request — разбор общий (214)."""
    return bool(СОБЫТИЯ_PR & workflow_on.события(text))


def _отступ(строка: str) -> int:
    return len(строка) - len(строка.lstrip())


def _блоки(строки: Sequence[str]) -> list[tuple[int, list[tuple[int, str]]]]:
    """Блоки `concurrency:` — номер строки ключа и его ПРЯМЫЕ дети.

    Уровень любой: группа отмены бывает и у прогона, и у джоба, и гонка у них
    одна (#151). Дети — строки ровно первого отступа под ключом: вложенный
    ключ чужого смысла за свой не примется.
    """
    найдено: list[tuple[int, list[tuple[int, str]]]] = []
    for номер, строка in enumerate(строки):
        ключ = _БЛОК.match(строка)
        if ключ is None:
            continue
        свой = len(ключ.group("отступ"))
        дети: list[tuple[int, str]] = []
        отступ_детей: int | None = None
        for след in range(номер + 1, len(строки)):
            текст = строки[след]
            if not текст.strip() or текст.lstrip().startswith("#"):
                continue
            if _отступ(текст) <= свой:
                break
            if отступ_детей is None:
                отступ_детей = _отступ(текст)
            if _отступ(текст) == отступ_детей:
                дети.append((след, текст))
        найдено.append((номер, дети))
    return найдено


def _называет_голову(группа: str) -> bool:
    """Голова — внутри выражения площадки, а не где-то в строке.

    Комментарий YAML срезается до разбора: ``x  # head.sha`` голову в группу
    не добавляет, а подстрочная проверка её там находила (#151).
    """
    значение = re.split(r"\s#", группа, maxsplit=1)[0]
    return any(ГОЛОВА in выражение for выражение in _ВЫРАЖЕНИЕ.findall(значение))


def check_text(text: str, path: str) -> list[Finding]:
    """Проверить один workflow: каждый блок отмены, на любом уровне."""
    if not listens_to_pr(text):
        return []

    находки: list[Finding] = []
    for номер_ключа, дети in _блоки(text.splitlines()):
        группа: str | None = None
        строка_группы = 0
        отмена: str | None = None
        for номер, строка in дети:
            совпадение = _GROUP.match(строка)
            if совпадение is not None:
                группа = совпадение.group("значение")
                строка_группы = номер + 1
            совпадение = _CANCEL.match(строка)
            if совпадение is not None:
                отмена = re.split(r"\s#", совпадение.group("значение"), maxsplit=1)[0]

        if отмена is None or отмена.strip().lower() in {"false", "'false'", '"false"'}:
            # Умолчание площадки — не отменять; сказанное вслух `false` тем более.
            continue

        if группа is None:
            находки.append(
                Finding(
                    path,
                    номер_ключа + 1,
                    "cancel-in-progress задан, а group — нет: площадка возьмёт "
                    "группу по умолчанию, и какой прогон окажется лишним, "
                    "предсказать нельзя",
                )
            )
            continue

        if _называет_голову(группа):
            continue

        находки.append(
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
        )
    return находки


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
