"""Единственная обязательная проверка ветки `main` — `PR check` (#6).

Ruleset на общей ветке требует проверки **по имени**, и это его главная
ловушка: имя попадает в настройку дословно. У нас матрица из девяти ячеек, и
список обязательного пришлось бы вести из одиннадцати имён вида
`гейты · ubuntu-latest · python 3.13`. Переименование ячейки — скажем, переход
на 3.14 — оставило бы в ruleset имя, которого больше нет: PR ждал бы проверку,
которая уже никогда не создастся, и ждал бы **вечно**. Issue #6 назвала эту
ловушку заранее, ещё до включения защиты.

Поэтому обязательное имя ровно одно и оно не меняется. Зелёным оно становится
только тогда, когда зелены все остальные.

## Почему чтением проверок, а не `needs:`

`needs:` живёт внутри одного workflow, а гейты у нас намеренно в трёх. Матрица
не должна перезапускаться от навешивания метки, а гейты разметки и changelog
обязаны — их ответ меняется именно метками. Свести всё в один файл ради `needs:`
значило бы либо гонять девять ячеек на каждую метку, либо оставить починку
«поставил метку» без зелёного.

## Почему джоб обязан отработать всегда

Для защиты ветки **пропущенный** джоб засчитывается как пройденный. Агрегатор,
написанный как `needs: [гейты]` с умолчанием по `if`, при падении зависимости
не краснеет, а пропускается — то есть ровно в тот момент, когда он обязан
запретить мерж, он его разрешает. Здесь джоб не зависит ни от чего и всегда
доходит до вердикта.

## Правила чтения — те же, что у очереди

Пустой список проверок это «CI не стартовал», а не «зелено»; считать по
уникальным именам; неполный набор — отказ. Живут они в `pr_ready`, и берутся
отсюда оттуда же, а не переписываются: два способа прочитать одно состояние
разойдутся, и разойдутся молча.

## Чего этот модуль не делает

Не судит о разметке, отставании и конфликте — это дело очереди. Здесь один
вопрос: зелены ли все проверки на голове PR.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gh_rest
import merge_queue
import pr_ready

#: Имя обязательной проверки. Совпадает с контекстом в ruleset **дословно** и
#: меняться не должно: переименование здесь равно снятию защиты с общей ветки,
#: потому что ruleset начнёт ждать имя, которого никто больше не создаёт.
SELF_NAME = "PR check"

#: Гейты, которые ходят только по `pull_request` и потому не попадают в эталон
#: с общей ветки: на `main` их прогонов нет вовсе.
#:
#: Список здесь второй раз называет то, что уже записано в workflow-файлах, и
#: сам по себе разошёлся бы с ними молча. Держит его не внимание, а тест
#: `test_pr_only_checks_совпадают_с_деревом`: он собирает имена джобов из всех
#: workflow, ходящих по `pull_request`, и требует совпадения.
PR_ONLY_CHECKS: frozenset[str] = frozenset(
    {
        "зона, тип, связь с задачей и имя ветки",
        "запись есть и она на языке проекта",
    }
)

EXIT_NOT_GREEN = 1

#: Сколько ждать остальных проверок. Матрица укладывается в минуту, но очередь
#: обновляет ветку, и прогон может начаться позже нашего.
DEFAULT_ATTEMPTS = 40
DEFAULT_INTERVAL = 15.0


@dataclass(frozen=True)
class Outcome:
    """Вердикт по проверкам головы PR."""

    ok: bool
    wait: bool
    problems: list[str]
    counted: int = 0


def verdict(
    checks: list[dict[str, Any]],
    expected: frozenset[str],
    *,
    self_name: str = SELF_NAME,
) -> Outcome:
    """Зелены ли все проверки, кроме этой самой.

    Себя проверка из рассмотрения убирает и из набора, и из эталона: она идёт,
    пока считает, и ждать собственного завершения значило бы не завершиться
    никогда.
    """
    ожидаемые = expected - {self_name}
    if not ожидаемые:
        return Outcome(
            ok=False,
            wait=False,
            problems=[
                "эталонного набора проверок нет — судить не по чему. Эталон это "
                "имена джобов последнего завершённого прогона ci на main плюс "
                "гейты, которые ходят только по pull_request"
            ],
        )

    последние = pr_ready.latest_by_name(checks)
    последние.pop(self_name, None)

    if not последние:
        return Outcome(
            ok=False,
            wait=True,
            problems=[
                "проверок на голове PR нет вовсе — это «CI не стартовал», а не "
                "«зелено»: сразу после пуша check-runs ещё не созданы"
            ],
        )

    недостающие = sorted(ожидаемые - set(последние))
    if недостающие:
        return Outcome(
            ok=False,
            wait=True,
            problems=[
                f"джобы не созданы: {', '.join(недостающие)}. Неполный набор — "
                "это «CI не стартовал», а не «зелено»"
            ],
            counted=len(последние),
        )

    идут = sorted(
        имя for имя, run in последние.items() if run.get("status") != "completed"
    )
    if идут:
        return Outcome(
            ok=False,
            wait=True,
            problems=[f"ещё идут: {', '.join(идут)}"],
            counted=len(последние),
        )

    красные = sorted(
        имя
        for имя, run in последние.items()
        if run.get("conclusion") not in {"success", "neutral", "skipped"}
    )
    if красные:
        return Outcome(
            ok=False,
            wait=False,
            problems=[
                f"не зелёные: {', '.join(красные)}. Проверок по уникальным "
                f"именам — {len(последние)}"
            ],
            counted=len(последние),
        )

    return Outcome(ok=True, wait=False, problems=[], counted=len(последние))


def run(
    fetch: Callable[[], list[dict[str, Any]]],
    expected: frozenset[str],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    interval: float = DEFAULT_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
) -> Outcome:
    """Дождаться остальных проверок и вынести вердикт.

    Ожидание ограничено: не дождавшись, проверка краснеет, а не висит. Красное
    здесь безопасная сторона — оно запрещает мерж, а зелёное разрешило бы.
    """
    итог = Outcome(ok=False, wait=True, problems=["ни одной попытки не сделано"])
    for попытка in range(1, max(attempts, 1) + 1):
        итог = verdict(fetch(), expected)
        if not итог.wait:
            return итог
        print(f"попытка {попытка}/{attempts}: {'; '.join(итог.problems)}")
        if попытка < attempts:
            sleep(interval)
    return Outcome(
        ok=False,
        wait=False,
        problems=[
            *итог.problems,
            f"ждали {attempts} раз по {interval:g} с и не дождались. Красное "
            "здесь безопаснее зелёного: оно запрещает мерж, а не разрешает",
        ],
        counted=итог.counted,
    )


def fetch_checks(repo: str, sha: str) -> list[dict[str, Any]]:
    """Check-runs головы PR. Отдельной функцией — чтобы вердикт был чистым."""
    ответ = gh_rest.request(
        "GET", f"/repos/{repo}/commits/{sha}/check-runs", params={"per_page": 100}
    )
    if not isinstance(ответ, dict):
        return []
    return [c for c in ответ.get("check_runs", []) if isinstance(c, dict)]


def main(argv: Sequence[str] | None = None) -> int:
    парсер = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    парсер.add_argument("--sha", required=True, help="голова PR")
    парсер.add_argument("--repo", default="", help="owner/repo")
    парсер.add_argument("--base", default="main", help="общая ветка для эталона")
    парсер.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    парсер.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    аргументы = парсер.parse_args(list(argv) if argv is not None else None)

    repo = аргументы.repo or gh_rest.repository()
    _, _, ci_имена = merge_queue.main_state(repo, аргументы.base)
    expected = ci_имена | PR_ONLY_CHECKS

    print(
        f"эталон: {len(ci_имена)} джобов ci с {аргументы.base} "
        f"плюс {len(PR_ONLY_CHECKS)} гейта только для PR"
    )

    итог = run(
        lambda: fetch_checks(repo, аргументы.sha),
        expected,
        attempts=аргументы.attempts,
        interval=аргументы.interval,
    )

    if итог.ok:
        print(f"зелено: проверок по уникальным именам {итог.counted}, все прошли")
        return 0

    for проблема in итог.problems:
        print(f"::error::{проблема}")
    print("PR check не зелёный — мерж запрещён", file=sys.stderr)
    return EXIT_NOT_GREEN


if __name__ == "__main__":
    raise SystemExit(main())
