"""Гейт разметки pull request: зона, тип, связь с задачей (#9).

`CLAUDE.md` требует метки на PR с первого дня, и до сих пор это требование
держалось **вниманием**: метки проставлялись руками, и забытая метка не красила
ничего. Замер в соседнем проекте показал, чем это кончается: навигационные
метки были у **4 PR из 12**, связь с задачей — тоже у 4. Машина метила PR
исправнее человека.

Вредит это трижды. PR без `Closes #N` не закрывает задачу при мерже — трекер
начинает врать. Зона работы видна до чтения диффа только по метке. И тип
изменения — единственное, что отличает правку документа от правки поведения,
пока дифф не прочитан.

## Откуда берутся данные

Из **события**, а не из API. Объект pull request целиком лежит в файле, на
который указывает ``GITHUB_EVENT_PATH``, и в нём уже есть и метки, и тело, и
признак форка. Это ноль запросов к площадке — ни REST, ни тем более GraphQL, —
и это же делает гейт проверяемым на подделанных данных: событие обычный JSON.

## Откуда берётся номенклатура

Из ``docs/labels.md``, а не из констант в этом файле. Номенклатура — предмет
документа, и второй её список рядом разошёлся бы с первым молча. Документа нет
или он не разбирается — гейт **падает**: проверка, не нашедшая предмета,
обязана сказать об этом, а не выйти зелёной.

## Строгость по месту

Отказ — только там, где PR действительно собираются мержить:

* **из форка** — предупреждение. Внешний участник не обязан знать наши метки,
  их проставит мейнтейнер; гейт про мерж, а не про воспитание;
* **черновик** — предупреждение. Разметку доводят к моменту готовности, и
  красное на черновике приучало бы читать красное как фон;
* **готовый PR не из форка** — отказ.

Граница проходит по достоверности: у готового PR отсутствие метки — факт, а не
догадка о намерении.
"""

from __future__ import annotations

import json
import os
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from utf8_output import force_utf8_output

ROOT = Path(__file__).resolve().parent.parent

#: Канонический документ по меткам: и состав зон, и состав типов берутся оттуда.
LABELS_DOC = "docs/labels.md"

#: Ненулевой код при отказе; 2 отделяет «проверку не удалось прогнать».
EXIT_FAILED = 1
EXIT_BROKEN = 2

#: GitHub закрывает задачу по трём глаголам, а не по одному. Требовать здесь
#: только `Closes` значило бы краснеть на законном `Fixes` — заворачивать верное.
_CLOSES_RE = re.compile(r"\b(?:closes|fixes|resolves)\s+#(\d+)", re.IGNORECASE)

#: Явный отказ от задачи. Причина обязана быть непустой: `docs/labels.md`
#: говорит прямо — «пустое поле причины равносильно отсутствию метки».
_NO_ISSUE_RE = re.compile(r"^\s*Без issue:\s*(?P<причина>\S.*)$", re.MULTILINE)

#: Частичное закрытие: связь есть, а задача остаётся открытой.
#:
#: Форма понадобилась на первом же применении гейта — к его собственному PR.
#: Задача #9 просит и гейт разметки, и метки конвейера, но метка заводится
#: только вместе с механизмом, которого ещё нет. Без этой формы оставалось два
#: выхода, и оба плохие: `Closes #9` закрыл бы наполовину сделанную задачу, а
#: `Без issue` соврал бы — задача есть.
#:
#: Пояснение обязательно и по той же причине, что причина у «Без issue»: «Часть
#: #9» без слов о том, какая именно часть, читателю говорит ровно столько же,
#: сколько отсутствие строки.
_PART_RE = re.compile(r"^\s*Часть #(\d+)\s*[—–-]\s*(?P<что>\S.*)$", re.MULTILINE)

#: Строка таблицы, у которой в первой ячейке метка в обратных кавычках.
_ROW_RE = re.compile(r"\|\s*`([^`]+)`\s*\|")

#: Приставка ветки, открытой агентским окном.
#:
#: Соглашение перенесено от соседа, где `agent/**` означает «ветку ведёт окно,
#: а не человек». Механизм соседа — открывалка PR под учёткой владельца — здесь
#: не нужен: окно открывает PR от имени владельца само. Нужно само имя: по нему
#: видно происхождение изменения без чтения коммитов, и на него можно повесить
#: правило площадки, не трогая ветки человека.
#:
#: Держится гейтом, а не вниманием, по частной причине: умолчание окна —
#: `claude/**`, оно возвращается при каждом перезапуске, и за одну серию его
#: пришлось поправлять трижды. Правило, которое приходится напоминать трижды,
#: вниманием не держится по определению.
AGENT_BRANCH_PREFIX = "agent/"


class LabelsUnreadableError(Exception):
    """Номенклатуру не удалось получить — проверять не с чем."""


@dataclass(frozen=True)
class Nomenclature:
    """Состав меток, вычитанный из документа."""

    areas: frozenset[str]
    types: frozenset[str]


@dataclass(frozen=True)
class Verdict:
    """Что не так с разметкой и чем это считать.

    Находки и предупреждения разделены списками, а не флагом внутри строки: от
    того, в какой список попала запись, зависит код возврата, и решать это
    разбором текста значило бы заводить вторую классификацию.
    """

    findings: list[str]
    warnings: list[str]

    @property
    def ok(self) -> bool:
        return not self.findings


def declared_labels(doc: str) -> Nomenclature:
    """Вынуть из ``docs/labels.md`` состав зон и типов.

    Разбираются только разделы «Зоны» и «Тип». Документ целиком брать нельзя:
    в служебном разделе тоже таблица с метками, и `wontfix` оказался бы типом
    изменения.
    """
    разделы: dict[str, list[str]] = {}
    заголовок = ""
    for строка in doc.splitlines():
        if строка.startswith("## "):
            заголовок = строка[3:].strip()
            разделы.setdefault(заголовок, [])
        elif заголовок:
            разделы[заголовок].append(строка)

    def метки(раздел: str) -> frozenset[str]:
        return frozenset(
            m.group(1)
            for m in (_ROW_RE.match(с) for с in разделы.get(раздел, []))
            if m is not None
        )

    areas, types = метки("Зоны"), метки("Тип")
    if not areas or not types:
        raise LabelsUnreadableError(
            f"в {LABELS_DOC} не нашлось разделов «Зоны» и «Тип» с таблицами меток "
            f"(зон {len(areas)}, типов {len(types)})"
        )
    return Nomenclature(areas=areas, types=types)


def pull_labels(pull: dict[str, object]) -> set[str]:
    """Имена меток PR. Незнакомая форма записи пропускается, а не роняет гейт."""
    raw = pull.get("labels")
    if not isinstance(raw, list):
        return set()
    имена: set[str] = set()
    for метка in raw:
        if isinstance(метка, dict):
            имя = метка.get("name")
            if isinstance(имя, str):
                имена.add(имя)
    return имена


def is_fork(pull: dict[str, object]) -> bool:
    """PR из форка. Отсутствующие или искажённые поля считаются «не форк»."""
    head = pull.get("head")
    if not isinstance(head, dict):
        return False
    repo = head.get("repo")
    return isinstance(repo, dict) and bool(repo.get("fork"))


def head_ref(pull: dict[str, object]) -> str:
    """Имя head-ветки PR. Отсутствие поля — не «пусто», а испорченное событие."""
    head = pull.get("head")
    if not isinstance(head, dict):
        return ""
    ref = head.get("ref")
    return ref if isinstance(ref, str) else ""


def branch_problems(ref: str) -> list[str]:
    """Годится ли имя ветки. Проверяется только у своих: см. `metadata_problems`."""
    if not ref:
        return [
            "у PR не видно head-ветки — гейт не нашёл предмета проверки. "
            "Это испорченное событие, а не «ветка в порядке»"
        ]
    if ref.startswith(AGENT_BRANCH_PREFIX) and len(ref) > len(AGENT_BRANCH_PREFIX):
        return []
    return [
        f"ветка `{ref}` не из `{AGENT_BRANCH_PREFIX}**` — так называются ветки, "
        "которые ведёт агентское окно. Head-ветку у открытого PR площадка менять "
        "не умеет: ветку придётся перепушить под верным именем и переоткрыть PR"
    ]


def metadata_problems(pull: dict[str, object], nomenclature: Nomenclature) -> list[str]:
    """Чего PR не хватает по правилу разметки. Одна строка — одна нехватка."""
    problems: list[str] = []
    labels = pull_labels(pull)

    зоны = sorted(labels & nomenclature.areas)
    самозванцы = sorted(
        м for м in labels if м.startswith("area/") and м not in nomenclature.areas
    )
    if самозванцы:
        problems.append(
            f"зоны нет в номенклатуре: {', '.join(самозванцы)}. Заведённые — "
            f"{', '.join(sorted(nomenclature.areas))}; новая заводится вместе с "
            f"задачей, которую некуда положить, а не опечаткой"
        )
    elif not зоны:
        problems.append(
            "нет метки area/* — по ней видно зону работы до чтения диффа "
            f"(заведены: {', '.join(sorted(nomenclature.areas))})"
        )
    elif len(зоны) > 1:
        problems.append(
            f"зон больше одной ({', '.join(зоны)}) — зона ровно одна; если честно "
            "не выбирается ни одна, задача слишком крупная"
        )

    типы = sorted(labels & nomenclature.types)
    if not типы:
        problems.append(
            f"нет метки типа работы (одна из: {', '.join(sorted(nomenclature.types))})"
        )
    elif len(типы) > 1:
        problems.append(f"типов больше одного ({', '.join(типы)}) — тип ровно один")

    body = pull.get("body")
    текст = body if isinstance(body, str) else ""
    связь = (
        _CLOSES_RE.search(текст) or _PART_RE.search(текст) or _NO_ISSUE_RE.search(текст)
    )
    if связь is None:
        problems.append(
            "в теле нет связи с задачей: ни «Closes #N», ни «Часть #N — <что "
            "именно>», ни «Без issue: <причина>» с непустым пояснением. Без неё "
            "задача не закроется при мерже, а решение не заводить задачу не "
            "оставит следа"
        )

    # Имя ветки спрашивается только у своих. У внешнего участника ветка
    # называется как ему удобно, и требовать от него нашего соглашения значило
    # бы заворачивать верное изменение из-за приставки.
    if not is_fork(pull):
        problems += branch_problems(head_ref(pull))

    return problems


def evaluate(event: dict[str, object], nomenclature: Nomenclature) -> Verdict:
    """Разобрать событие и вынести вердикт.

    Событие не про pull request — не находка: workflow могли позвать иначе. Но
    и тихо «чисто» здесь возвращать нельзя, поэтому случай назван
    предупреждением, а не пропущен молча.
    """
    pull = event.get("pull_request")
    if not isinstance(pull, dict):
        return Verdict(
            [], ["событие не про pull request — разметку проверять не на чем"]
        )

    problems = metadata_problems(pull, nomenclature)
    if not problems:
        return Verdict([], [])

    номер = pull.get("number")
    подпись = f"PR #{номер}" if isinstance(номер, int) else "PR"
    сводка = "; ".join(problems)

    if is_fork(pull):
        return Verdict(
            [], [f"{подпись} из форка: {сводка}. Метки проставит мейнтейнер"]
        )
    if pull.get("draft"):
        return Verdict(
            [], [f"{подпись} черновик: {сводка}. Довести до снятия черновика"]
        )
    return Verdict([f"{подпись}: {problem}" for problem in problems], [])


def main(argv: Sequence[str] | None = None) -> int:
    """Прочитать событие и вернуть ненулевой код, если разметки не хватает."""
    force_utf8_output()

    аргументы = list(argv or [])
    путь = аргументы[0] if аргументы else os.environ.get("GITHUB_EVENT_PATH", "")
    if not путь:
        print(
            "гейт не отработал: не задан путь к событию — ни аргументом, "
            "ни GITHUB_EVENT_PATH",
            file=sys.stderr,
        )
        return EXIT_BROKEN

    try:
        nomenclature = declared_labels((ROOT / LABELS_DOC).read_text(encoding="utf-8"))
    except (OSError, LabelsUnreadableError) as exc:
        print(f"гейт не отработал: номенклатура не прочитана — {exc}", file=sys.stderr)
        return EXIT_BROKEN

    try:
        событие = json.loads(Path(путь).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"гейт не отработал: событие не прочитано — {exc}", file=sys.stderr)
        return EXIT_BROKEN
    if not isinstance(событие, dict):
        print("гейт не отработал: событие не объект", file=sys.stderr)
        return EXIT_BROKEN

    вердикт = evaluate(событие, nomenclature)

    for предупреждение in вердикт.warnings:
        print(f"::warning::{предупреждение}")
    for находка in вердикт.findings:
        print(f"::error::{находка}")

    if вердикт.ok:
        итог = (
            f"разметка: зон {len(nomenclature.areas)}, типов "
            f"{len(nomenclature.types)} в номенклатуре, у PR всё на месте"
        )
        if вердикт.warnings:
            итог = f"разметка не проверялась, замечаний {len(вердикт.warnings)}"
        print(итог)
        return 0

    print(
        f"\nразметки не хватает: {len(вердикт.findings)}. Номенклатура — {LABELS_DOC}",
        file=sys.stderr,
    )
    return EXIT_FAILED


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
