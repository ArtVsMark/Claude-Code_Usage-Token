"""Единственная обязательная проверка ветки `main` — `PR check` (#6, #46).

Ruleset на общей ветке требует проверки **по имени**, и имя попадает в
настройку дословно. У нас матрица из девяти ячеек, и список обязательного
пришлось бы вести из одиннадцати имён вида `гейты · ubuntu-latest · python
3.13`. Переименование ячейки оставило бы в ruleset имя, которого больше никто
не создаёт: PR ждал бы такую проверку **вечно**. Поэтому обязательное имя ровно
одно и оно не меняется, а зелёным становится, только когда зелены все
остальные.

## Что спрашивается и почему именно это

**Прогоны на голове PR, а не имена джобов.** Первая редакция сверяла имена
проверок с эталоном — именами джобов последнего прогона `ci` на `main`. Это
работало ровно до первого PR, который состав матрицы **меняет**: подъём Python
с 3.13 на 3.14 даёт другие имена, эталон приходит из прошлого и требует имени,
которого на этом PR нет и не будет. Вердикт «джобы не созданы» становился
вечным, а починить это в PR было нельзя — чтобы эталон обновился, изменение
должно сначала уехать в `main`, а уехать оно не могло.

То есть ловушка #6 воспроизводилась этажом ниже: там имя джоба входило дословно
в настройку площадки, здесь — в эталон, взятый с другой ветки.

**Состав берётся из дерева этого же PR.** Прогон работает на его checkout,
поэтому вопрос «какие workflow обязаны были стартовать» решается чтением
`.github/workflows/` — того самого состояния, которое и проверяется. Ни второго
списка, ни эталона с чужой ветки.

**Сверка идёт по пути файла, а не по имени.** `name:` — проза, её меняют;
путь — адрес. Переименование workflow перестаёт что-либо значить.

## Правила чтения остаются прежними

Пустой набор — это «CI не стартовал», а не «зелено»: прогона на этот SHA нет —
отказ, а не согласие. Незавершённый прогон — ожидание. Отменённый — красное:
результата нет.

## Чего этот модуль не делает

Не судит о разметке, отставании и конфликте — это дело очереди. Здесь один
вопрос: все ли прогоны на голове PR прошли.
"""

from __future__ import annotations

import argparse
import pathlib
import re
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gh_rest

#: Имя обязательной проверки. Совпадает с контекстом в ruleset **дословно** и
#: меняться не должно: переименование здесь равно снятию защиты с общей ветки,
#: потому что ruleset начнёт ждать имя, которого никто больше не создаёт.
SELF_NAME = "PR check"

#: Путь к собственному workflow. Себя проверка из состава убирает: она идёт,
#: пока считает, и ждать собственного завершения значило бы не завершиться.
SELF_PATH = ".github/workflows/pr-check.yml"

WORKFLOWS_DIR = ".github/workflows"

EXIT_NOT_GREEN = 1

#: Сколько ждать остальных прогонов. Матрица укладывается в минуту, но очередь
#: обновляет ветку, и прогон может начаться позже нашего.
DEFAULT_ATTEMPTS = 40
DEFAULT_INTERVAL = 15.0

#: Заключения, которые считаются пройденными. `cancelled` сюда не входит
#: намеренно: результата нет, а красное — безопасная сторона.
GREEN = frozenset({"success", "neutral", "skipped"})

#: Строка `on:` в начале файла. Кавычки допускаются: в YAML 1.1 голое `on` —
#: булево, и часть проектов пишет ключ в кавычках именно поэтому.
_ON_LINE = re.compile(r"^(?:on|'on'|\"on\")\s*:(.*)$")

#: Строка блока `on:`, объявляющая событие `pull_request` и ничего больше.
#: `pull_request_target` не совпадает: после имени допускается только двоеточие.
_PR_EVENT = re.compile(r"^\s+-?\s*pull_request\s*:?\s*$")


@dataclass(frozen=True)
class Outcome:
    """Вердикт по прогонам головы PR."""

    ok: bool
    wait: bool
    problems: list[str]
    counted: int = 0


def triggers_on_pull_request(text: str) -> bool:
    """Ходит ли workflow по `pull_request`.

    Разбирается только блок `on:` — три его законные формы: скаляр
    (`on: pull_request`), поток (`on: [push, pull_request]`) и блок. Полного
    разбора YAML здесь нет намеренно: гейт обходится стандартной библиотекой, а
    зависимость сделала бы обязательную проверку заложницей сборки.
    """
    lines = text.splitlines()
    for номер, строка in enumerate(lines):
        совпадение = _ON_LINE.match(строка)
        if совпадение is None:
            continue
        хвост = совпадение.group(1).split("#", 1)[0].strip()
        if хвост:
            return "pull_request" in re.split(r"[\s,\[\]]+", хвост)
        for след in lines[номер + 1 :]:
            if след.strip() and not след.startswith((" ", "\t")):
                return False
            if _PR_EVENT.match(след):
                return True
        return False
    return False


def expected_workflows(root: Path, *, self_path: str = SELF_PATH) -> frozenset[str]:
    """Пути workflow, которые обязаны стартовать на этом PR.

    Читаются из дерева **самого PR**: именно его состояние и проверяется.
    Добавленный workflow попадает сюда сразу, удалённый — исчезает; ни то, ни
    другое больше не требует, чтобы изменение сначала уехало в общую ветку.
    """
    каталог = root / WORKFLOWS_DIR
    пути = sorted(каталог.glob("*.yml")) + sorted(каталог.glob("*.yaml"))
    найдено = {
        f"{WORKFLOWS_DIR}/{путь.name}"
        for путь in пути
        if triggers_on_pull_request(путь.read_text(encoding="utf-8"))
    }
    return frozenset(найдено - {self_path})


def meaningful(run: dict[str, Any]) -> bool:
    """Несёт ли прогон вердикт. Отменённый — не несёт.

    `cancelled` означает «не досчитали», а не «не прошло». Вытесненный
    concurrency-группой прогон — обычное дело: метку навесили, событие пришло
    заново, старый прогон убили. Считать это красным значит краснеть на
    штатной работе площадки.
    """
    return run.get("status") != "completed" or run.get("conclusion") != "cancelled"


def latest_by_path(runs: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Свежий ЗНАЧАЩИЙ прогон на каждый путь workflow.

    ## Почему не просто «первый в списке»

    Так и было написано — и покраснело на собственном PR через двадцать минут
    после появления. Площадка отдала для `pr-metadata.yml` и `changelog.yml`
    сначала **отменённый** прогон, а успешный — ниже по списку. Порядок выдачи
    не гарантирует, что первым идёт тот, у которого есть вердикт.

    Поэтому порядок задаётся здесь явно — по времени создания, — а отменённый
    прогон уступает место любому значащему: он не «красный», он «без ответа».
    Если значащего нет вовсе, остаётся отменённый, и вердикт по нему не
    зелёный — иначе вытесненный набор сходил бы за пройденный.
    """
    последние: dict[str, dict[str, Any]] = {}
    по_времени = sorted(
        runs, key=lambda r: str(r.get("created_at") or ""), reverse=True
    )
    for run in по_времени:
        путь = run.get("path")
        if not isinstance(путь, str):
            continue
        текущий = последние.get(путь)
        if текущий is None or (meaningful(run) and not meaningful(текущий)):
            последние[путь] = run
    return последние


def verdict(runs: list[dict[str, Any]], expected: frozenset[str]) -> Outcome:
    """Все ли обязательные прогоны на голове PR прошли."""
    if not expected:
        return Outcome(
            ok=False,
            wait=False,
            problems=[
                "в дереве нет ни одного workflow, ходящего по pull_request — "
                "проверка осталась без предмета. Это поломка, а не «зелено»"
            ],
        )

    последние = latest_by_path(runs)

    недостающие = sorted(expected - set(последние))
    if недостающие:
        return Outcome(
            ok=False,
            wait=True,
            problems=[
                f"прогонов нет: {', '.join(недостающие)}. Отсутствие прогона — "
                "это «CI не стартовал», а не «зелено»"
            ],
            counted=len(последние),
        )

    идут = sorted(
        путь for путь in expected if последние[путь].get("status") != "completed"
    )
    if идут:
        return Outcome(
            ok=False,
            wait=True,
            problems=[f"ещё идут: {', '.join(идут)}"],
            counted=len(последние),
        )

    красные = sorted(
        f"{путь}={последние[путь].get('conclusion')}"
        for путь in expected
        if последние[путь].get("conclusion") not in GREEN
    )
    if красные:
        return Outcome(
            ok=False,
            wait=False,
            problems=[f"не зелёные: {', '.join(красные)}"],
            counted=len(последние),
        )

    return Outcome(ok=True, wait=False, problems=[], counted=len(expected))


def run(
    fetch: Callable[[], list[dict[str, Any]]],
    expected: frozenset[str],
    *,
    attempts: int = DEFAULT_ATTEMPTS,
    interval: float = DEFAULT_INTERVAL,
    sleep: Callable[[float], None] = time.sleep,
) -> Outcome:
    """Дождаться остальных прогонов и вынести вердикт.

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


def fetch_runs(repo: str, sha: str) -> list[dict[str, Any]]:
    """Прогоны на голове PR. Отдельной функцией — чтобы вердикт был чистым."""
    ответ = gh_rest.request(
        "GET",
        f"/repos/{repo}/actions/runs",
        params={"head_sha": sha, "event": "pull_request", "per_page": 100},
    )
    if not isinstance(ответ, dict):
        return []
    return [r for r in ответ.get("workflow_runs", []) if isinstance(r, dict)]


def main(argv: Sequence[str] | None = None) -> int:
    парсер = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    парсер.add_argument("--sha", required=True, help="голова PR")
    парсер.add_argument("--repo", default="", help="owner/repo")
    парсер.add_argument("--root", type=Path, default=Path.cwd(), help="корень дерева")
    парсер.add_argument("--attempts", type=int, default=DEFAULT_ATTEMPTS)
    парсер.add_argument("--interval", type=float, default=DEFAULT_INTERVAL)
    аргументы = парсер.parse_args(list(argv) if argv is not None else None)

    repo = аргументы.repo or gh_rest.repository()
    expected = expected_workflows(аргументы.root)
    print(
        f"обязательных прогонов {len(expected)} "
        f"(из дерева этого PR): {', '.join(sorted(expected))}"
    )

    итог = run(
        lambda: fetch_runs(repo, аргументы.sha),
        expected,
        attempts=аргументы.attempts,
        interval=аргументы.interval,
    )

    if итог.ok:
        print(f"зелено: прогонов {итог.counted}, все прошли")
        return 0

    for проблема in итог.problems:
        print(f"::error::{проблема}")
    print("PR check не зелёный — мерж запрещён", file=sys.stderr)
    return EXIT_NOT_GREEN


if __name__ == "__main__":
    raise SystemExit(main())
