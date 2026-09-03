"""Проверки обязательной проверки `PR check` (#6, #46).

Здесь дороже всего ложное «зелено»: `PR check` — единственное, что стоит между
красным изменением и защищённой `main`. Ошибись он в сторону зелёного — и
ruleset пропустит то, ради запрета чего он включён.

Поэтому почти все проверки ниже — про **отказы и ожидание**: удачный путь виден
по любому прогону, а отказы не видны никогда, пока их не подделать.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pr_check

КОРЕНЬ = Path(__file__).resolve().parents[1]
WORKFLOWS = КОРЕНЬ / pr_check.WORKFLOWS_DIR

ЖДЁМ = frozenset({".github/workflows/ci.yml", ".github/workflows/разметка.yml"})


def _run(
    путь: str, *, status: str = "completed", conclusion: str = "success"
) -> dict[str, Any]:
    return {"path": путь, "status": status, "conclusion": conclusion}


def _все_зелёные() -> list[dict[str, Any]]:
    return [_run(п) for п in sorted(ЖДЁМ)]


# ── состав берётся из дерева, а не с чужой ветки ──────────────────────────


def test_состав_читается_из_дерева_pr() -> None:
    """Именно из этого дерева: оно и есть то, что проверяется.

    Эталон с общей ветки ломал любой PR, который состав проверок меняет.
    """
    состав = pr_check.expected_workflows(КОРЕНЬ)

    assert состав, "в дереве проекта есть workflow по pull_request"
    assert pr_check.SELF_PATH not in состав, "себя проверка ждать не должна"
    assert ".github/workflows/ci.yml" in состав


def test_переименование_ячейки_матрицы_больше_не_ломает(tmp_path: Path) -> None:
    """Ради чего всё и переделано (#46).

    Раньше PR, поднимающий Python с 3.13 на 3.14, давал другие имена джобов,
    эталон приходил из прошлого, и вердикт «джобы не созданы» был вечным.
    Теперь имена джобов не спрашиваются вовсе: сверка идёт по прогонам.
    """
    итог = pr_check.verdict(_все_зелёные(), ЖДЁМ)

    assert итог.ok


def test_путь_а_не_имя() -> None:
    """`name:` — проза, её меняют; путь — адрес."""
    состав = pr_check.expected_workflows(КОРЕНЬ)

    assert all(п.startswith(pr_check.WORKFLOWS_DIR + "/") for п in состав)


# ── разбор `on:` ──────────────────────────────────────────────────────────


def test_блочная_форма() -> None:
    assert pr_check.triggers_on_pull_request(
        "name: x\non:\n  pull_request:\n    types: [opened]\n"
    )


def test_поток_и_скаляр() -> None:
    assert pr_check.triggers_on_pull_request("on: [push, pull_request]\n")
    assert pr_check.triggers_on_pull_request("on: pull_request\n")


def test_ключ_в_кавычках() -> None:
    """В YAML 1.1 голое `on` — булево, и ключ пишут в кавычках именно поэтому."""
    assert pr_check.triggers_on_pull_request('"on":\n  pull_request:\n')
    assert pr_check.triggers_on_pull_request("'on':\n  pull_request:\n")


def test_другое_событие_не_считается() -> None:
    assert not pr_check.triggers_on_pull_request("on:\n  push:\n    branches: [main]\n")
    assert not pr_check.triggers_on_pull_request(
        "on:\n  schedule:\n    - cron: '0 1 * * *'\n"
    )


def test_pull_request_target_это_не_pull_request() -> None:
    """Другое событие с похожим именем — самая вероятная ошибка разбора."""
    assert not pr_check.triggers_on_pull_request("on:\n  pull_request_target:\n")


def test_упоминание_в_комментарии_и_ниже_блока_не_считается() -> None:
    assert not pr_check.triggers_on_pull_request(
        "on:\n  push:\n# pull_request:\njobs:\n  x:\n    if: pull_request\n"
    )


def test_живые_workflow_разбираются_как_ожидается() -> None:
    """Разбор проверяется на настоящих файлах, а не только на выдумке."""
    по_событию = {
        путь.name: pr_check.triggers_on_pull_request(путь.read_text(encoding="utf-8"))
        for путь in sorted(WORKFLOWS.glob("*.yml"))
    }

    assert по_событию["ci.yml"]
    assert по_событию["pr-metadata.yml"]
    assert по_событию["changelog.yml"]
    assert not по_событию["release.yml"]
    assert not по_событию["merge-queue.yml"]


# ── вердикт ───────────────────────────────────────────────────────────────


def test_дерева_без_workflow_это_отказ(tmp_path: Path) -> None:
    """Проверка, не нашедшая предмета, обязана сказать об этом.

    Переезд каталога иначе отключил бы её молча — зелёной навсегда.
    """
    итог = pr_check.verdict(_все_зелёные(), frozenset())

    assert not итог.ok
    assert not итог.wait
    assert "без предмета" in итог.problems[0]


def test_прогона_нет_это_ждать_а_не_зелено() -> None:
    итог = pr_check.verdict([], ЖДЁМ)

    assert not итог.ok
    assert итог.wait
    assert "CI не стартовал" in итог.problems[0]


def test_неполный_набор_это_ждать() -> None:
    итог = pr_check.verdict([_run(".github/workflows/ci.yml")], ЖДЁМ)

    assert not итог.ok
    assert итог.wait
    assert "разметка.yml" in итог.problems[0]


def test_идущий_прогон_это_ждать() -> None:
    прогоны = _все_зелёные()
    прогоны[0] = _run(прогоны[0]["path"], status="in_progress", conclusion="")

    итог = pr_check.verdict(прогоны, ЖДЁМ)

    assert not итог.ok
    assert итог.wait


def test_красный_прогон_это_отказ_а_не_ожидание() -> None:
    прогоны = _все_зелёные()
    прогоны[0] = _run(прогоны[0]["path"], conclusion="failure")

    итог = pr_check.verdict(прогоны, ЖДЁМ)

    assert not итог.ok
    assert not итог.wait
    assert "failure" in итог.problems[0]


def test_отменённый_прогон_считается_красным() -> None:
    """`cancelled` — не «зелено» и не «пропущено»: результата нет."""
    прогоны = _все_зелёные()
    прогоны[0] = _run(прогоны[0]["path"], conclusion="cancelled")

    итог = pr_check.verdict(прогоны, ЖДЁМ)

    assert not итог.ok
    assert not итог.wait


def test_лишний_прогон_не_мешает() -> None:
    """Прогон, которого нет в составе, вердикта не меняет.

    Иначе сторонний workflow, добавленный площадкой, вешал бы проверку.
    """
    прогоны = [
        *_все_зелёные(),
        _run(".github/workflows/чужой.yml", conclusion="failure"),
    ]

    assert pr_check.verdict(прогоны, ЖДЁМ).ok


def test_вытесненный_прогон_не_воскрешает_красное() -> None:
    """Площадка отдаёт список от свежего к старому, побеждает первый."""
    свежие = _все_зелёные()
    старые = [_run(r["path"], conclusion="cancelled") for r in свежие]

    итог = pr_check.verdict([*свежие, *старые], ЖДЁМ)

    assert итог.ok


def test_отменённый_прогон_уступает_успешному() -> None:
    """Живые данные, на которых первая редакция покраснела (#50).

    Площадка отдала для `pr-metadata.yml` сначала ОТМЕНЁННЫЙ прогон, а
    успешный — ниже по списку. «Первый в списке победил» взял отменённый, и
    обязательная проверка покраснела на собственном PR через двадцать минут
    после того, как была написана.

    `cancelled` означает «не досчитали», а не «не прошло»: вытеснение
    concurrency-группой — штатная работа площадки, метку навесили и событие
    пришло заново.
    """
    прогоны = [
        {
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-02T09:30:00Z",
        },
        {
            "path": ".github/workflows/разметка.yml",
            "status": "completed",
            "conclusion": "cancelled",
            "created_at": "2026-09-02T09:32:00Z",
        },
        {
            "path": ".github/workflows/разметка.yml",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-02T09:31:00Z",
        },
    ]

    assert pr_check.verdict(прогоны, ЖДЁМ).ok


def test_свежий_отказ_не_перебивается_старым_успехом() -> None:
    """Обратная сторона: у отказа вердикт ЕСТЬ, и он свежее."""
    прогоны = [
        {
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-02T09:30:00Z",
        },
        {
            "path": ".github/workflows/разметка.yml",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-02T09:31:00Z",
        },
        {
            "path": ".github/workflows/разметка.yml",
            "status": "completed",
            "conclusion": "failure",
            "created_at": "2026-09-02T09:32:00Z",
        },
    ]

    итог = pr_check.verdict(прогоны, ЖДЁМ)

    assert not итог.ok and not итог.wait


def test_все_прогоны_отменены_это_не_зелено() -> None:
    """Значащего прогона нет вовсе — значит ничто не подтвердило голову."""
    прогоны = [_run(п, conclusion="cancelled") for п in sorted(ЖДЁМ)]

    итог = pr_check.verdict(прогоны, ЖДЁМ)

    assert not итог.ok


def test_идущий_прогон_важнее_старого_успеха() -> None:
    """У нового прогона вердикт будет — значит его и ждём."""
    прогоны: list[dict[str, Any]] = [
        {
            "path": ".github/workflows/ci.yml",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-02T09:30:00Z",
        },
        {
            "path": ".github/workflows/разметка.yml",
            "status": "completed",
            "conclusion": "success",
            "created_at": "2026-09-02T09:31:00Z",
        },
        {
            "path": ".github/workflows/разметка.yml",
            "status": "in_progress",
            "conclusion": None,
            "created_at": "2026-09-02T09:32:00Z",
        },
    ]

    итог = pr_check.verdict(прогоны, ЖДЁМ)

    assert not итог.ok and итог.wait


def test_всё_зелено() -> None:
    итог = pr_check.verdict(_все_зелёные(), ЖДЁМ)

    assert итог.ok
    assert итог.counted == len(ЖДЁМ)


# ── ожидание ──────────────────────────────────────────────────────────────


def test_ждёт_и_дожидается() -> None:
    ответы = [[], [_run(".github/workflows/ci.yml")], _все_зелёные()]
    паузы: list[float] = []

    итог = pr_check.run(
        lambda: ответы.pop(0),
        ЖДЁМ,
        attempts=5,
        interval=7,
        sleep=паузы.append,
    )

    assert итог.ok
    assert паузы == [7, 7]


def test_не_ждёт_вечно() -> None:
    """Не дождавшись, проверка краснеет, а не висит."""
    итог = pr_check.run(list, ЖДЁМ, attempts=3, interval=1, sleep=lambda _: None)

    assert not итог.ok
    assert not итог.wait
    assert any("не дождались" in п for п in итог.problems)


def test_красное_прерывает_ожидание_сразу() -> None:
    обращений = 0

    def fetch() -> list[dict[str, Any]]:
        nonlocal обращений
        обращений += 1
        прогоны = _все_зелёные()
        прогоны[0] = _run(прогоны[0]["path"], conclusion="failure")
        return прогоны

    итог = pr_check.run(fetch, ЖДЁМ, attempts=9, interval=1, sleep=lambda _: None)

    assert not итог.ok
    assert обращений == 1


# ── имя не разошлось с настройкой площадки ────────────────────────────────


def test_имя_джоба_совпадает_с_обязательным_контекстом() -> None:
    """Переименование джоба равносильно снятию защиты с `main`.

    Ruleset требует контекст `PR check` дословно. Разойдись имя джоба с ним —
    и ни один PR не станет мержимым: площадка будет ждать проверку, которой
    никто не создаёт.
    """
    текст = (КОРЕНЬ / pr_check.SELF_PATH).read_text(encoding="utf-8")

    assert f"\n    name: {pr_check.SELF_NAME}\n" in текст


def test_путь_к_себе_существует() -> None:
    assert (КОРЕНЬ / pr_check.SELF_PATH).is_file()
