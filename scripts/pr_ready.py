"""Вердикт по pull request: можно ли его мержить (#8).

Три правила из `CLAUDE.md`, § «Как читать результат проверок», здесь перестают
быть намерением и становятся кодом. Все три противоречат интуиции, и каждое
уже стоило кому-то ошибки.

**Пустой список проверок — это «CI не стартовал», а не «зелено».** Сразу после
пуша check-runs ещё не созданы, и условие «нет красных, нет ожидающих»
выполняется на пустоте идеально. Поэтому пустой набор — отказ, а не согласие.

**«Зелено на моей ветке» ≠ «зелено после мержа».** Отставшая ветка проверена на
состоянии, которого после мержа не будет: её зелёный отвечает про вчера. Такой
PR не «готов», он «обнови и приходи».

**Конфликт — это «проверок нет вовсе», а не «CI сломался».** Прогон на PR идёт
по merge-коммиту; слияние невозможно — проверки не создаются. Конфликтный PR
поэтому не ждут, а метят и пропускают: очередь, падающая на нём, стоит вся.

И четвёртое, про сами проверки: **считать по уникальным именам.** После
обновления ветки GitHub создаёт второй комплект check-runs, а первый остаётся
на коммите. Суммарное число удваивается, и на этом уже был неверный вывод —
«32 проверки» вместо шестнадцати, продержавшийся сутки.

## Чем набор сверяется с эталоном

Именами с последнего завершённого прогона на `main`. Отсутствующее имя означает
«джоб не создан» — тот самый случай, который проверку на пустоте и обманывает.
Без эталона судить не по чему, поэтому пустой эталон — тоже отказ.

## Чего этот модуль не делает

Не ходит в сеть. Ему отдают снимок состояния, он возвращает вердикт — иначе
проверить его на подделанных данных было бы нечем, а проверять здесь надо
именно отказы.
"""

from __future__ import annotations

import pathlib
import sys
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import check_pr_metadata

#: Стоп-метка. Сильнее всего остального: решение человека, а не состояние.
HOLD = "hold"

#: Видимый признак «уедет по зелёному». Умолчание — мержить, поэтому метка
#: ставится автоматически, а несогласие выражается меткой `hold`.
MERGE_WHEN_GREEN = "merge-when-green"

#: Конфликт. Очередь такой PR пропускает, а не ждёт на нём.
NEEDS_REBASE = "needs-rebase"

PIPELINE_LABELS = frozenset({HOLD, MERGE_WHEN_GREEN, NEEDS_REBASE})

#: Состояния вердикта. Разделены намеренно: «ещё не готов» и «не поедет
#: никогда без человека» требуют разных действий от очереди, и склеить их
#: значило бы либо ждать вечно, либо мержить преждевременно.
READY = "готов"
WAIT = "ждать"
CONFLICT = "конфликт"
STALE = "отстал"
HELD = "придержан"
BLOCKED = "не поедет"


@dataclass(frozen=True)
class Snapshot:
    """Состояние, по которому выносится вердикт.

    Собирается вызывающим одним проходом по API: сюда приходят уже данные, а
    не способ их получить.
    """

    pull: dict[str, Any]
    checks: list[dict[str, Any]] = field(default_factory=list)
    expected: frozenset[str] = frozenset()
    main_busy: bool = False
    main_red: bool = False


@dataclass(frozen=True)
class Verdict:
    """Что с PR и почему. Причина обязательна даже у «готов»."""

    state: str
    reasons: list[str]

    @property
    def ready(self) -> bool:
        return self.state == READY


def labels(pull: dict[str, Any]) -> set[str]:
    """Метки PR — тем же разбором, что и у гейта разметки."""
    return check_pr_metadata.pull_labels(pull)


def latest_by_name(checks: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Последний check-run на каждое **имя**.

    Именно здесь живёт правило про уникальные имена. Список приходит от
    площадки в порядке убывания свежести, поэтому побеждает первый встреченный:
    старый комплект, оставшийся на коммите после обновления ветки, не
    учитывается дважды и не воскрешает вчерашнее красное.
    """
    последние: dict[str, dict[str, Any]] = {}
    for run in checks:
        имя = run.get("name")
        if isinstance(имя, str) and имя not in последние:
            последние[имя] = run
    return последние


def check_problems(snapshot: Snapshot) -> tuple[list[str], bool]:
    """Что не так с проверками. Второе значение — «надо ещё подождать»."""
    последние = latest_by_name(snapshot.checks)

    if not snapshot.expected:
        return (
            [
                "эталонного набора проверок нет — судить не по чему. Эталон это "
                "имена с последнего завершённого прогона на main"
            ],
            False,
        )

    if not последние:
        return (
            [
                "проверок на голове PR нет вовсе — это «CI не стартовал», а не "
                "«зелено»: сразу после пуша check-runs ещё не созданы"
            ],
            True,
        )

    недостающие = sorted(snapshot.expected - set(последние))
    if недостающие:
        return (
            [
                f"джобы не созданы: {', '.join(недостающие)}. Неполный набор — "
                "это «CI не стартовал», а не «зелено»"
            ],
            True,
        )

    идут = sorted(
        имя for имя, run in последние.items() if run.get("status") != "completed"
    )
    if идут:
        return ([f"проверки ещё идут: {', '.join(идут)}"], True)

    красные = sorted(
        имя
        for имя, run in последние.items()
        if run.get("conclusion") not in {"success", "neutral", "skipped"}
    )
    if красные:
        return (
            [
                f"проверки не зелёные: {', '.join(красные)}. Проверок по "
                f"уникальным именам — {len(последние)}"
            ],
            False,
        )

    return ([], False)


def evaluate(snapshot: Snapshot) -> Verdict:
    """Вынести вердикт по снимку состояния."""
    pull = snapshot.pull
    метки = labels(pull)

    if pull.get("state") != "open":
        return Verdict(BLOCKED, ["PR не открыт"])

    if HOLD in метки:
        return Verdict(HELD, [f"стоит метка {HOLD} — решение владельца, сильнее всего"])

    if pull.get("draft"):
        return Verdict(BLOCKED, ["PR черновик — автор ещё не объявил его готовым"])

    # Конфликт проверяется до всего, что связано с проверками: их на
    # конфликтном PR не существует, и «ждать зелёного» означало бы ждать вечно.
    if pull.get("mergeable_state") == "dirty" or pull.get("mergeable") is False:
        return Verdict(
            CONFLICT,
            [
                "слияние невозможно: проверок на таком PR не создаётся вовсе, и "
                "ожидание зелёного здесь бесконечно"
            ],
        )

    if pull.get("mergeable") is None:
        return Verdict(WAIT, ["площадка ещё не досчитала слияние"])

    разметка = check_pr_metadata.metadata_problems(pull, _nomenclature())
    if разметка:
        return Verdict(BLOCKED, [f"разметка: {'; '.join(разметка)}"])

    if snapshot.main_red:
        return Verdict(
            WAIT,
            [
                "последний прогон на main красный — пока он такой, следующий PR "
                "не мержится: иначе красный main копит изменения, и разбирать "
                "придётся смесь"
            ],
        )

    if snapshot.main_busy:
        return Verdict(
            WAIT,
            [
                "на main идёт прогон — мерж внахлёст вытеснил бы ожидающий "
                "прогон, и состояние main уехало бы без единой проверки"
            ],
        )

    if pull.get("mergeable_state") == "behind":
        return Verdict(
            STALE,
            [
                "ветка отстала от main: её зелёный отвечает про состояние, "
                "которого после мержа не будет"
            ],
        )

    проблемы, ждать = check_problems(snapshot)
    if проблемы:
        return Verdict(WAIT if ждать else BLOCKED, проблемы)

    имена = latest_by_name(snapshot.checks)
    return Verdict(
        READY,
        [
            f"проверок по уникальным именам {len(имена)}, все зелёные; "
            f"эталон сошёлся ({len(snapshot.expected)} имён)"
        ],
    )


def _nomenclature() -> check_pr_metadata.Nomenclature:
    """Номенклатура меток из документа — один источник на весь конвейер."""
    документ = check_pr_metadata.ROOT / check_pr_metadata.LABELS_DOC
    return check_pr_metadata.declared_labels(документ.read_text(encoding="utf-8"))
