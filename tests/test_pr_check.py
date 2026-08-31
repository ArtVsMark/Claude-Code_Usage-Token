"""Проверки обязательной проверки `PR check` (#6).

Здесь дороже всего ложное «зелено»: `PR check` — единственное, что стоит между
красным изменением и защищённой `main`. Ошибись он в сторону зелёного — и
ruleset пропустит то, ради запрета чего он и включён.

Поэтому почти все проверки ниже — про **отказы и ожидание**, а не про удачный
путь: удачный путь виден по любому прогону, а отказы не видны никогда, пока их
не подделать.
"""

from __future__ import annotations

import re
from pathlib import Path

import pr_check

КОРЕНЬ = Path(__file__).resolve().parents[1]
WORKFLOWS = КОРЕНЬ / ".github" / "workflows"

ЭТАЛОН = frozenset({"гейты · ubuntu-latest · python 3.12", *pr_check.PR_ONLY_CHECKS})


def _run(
    имя: str, *, status: str = "completed", conclusion: str = "success"
) -> dict[str, str]:
    return {"name": имя, "status": status, "conclusion": conclusion}


def _все_зелёные() -> list[dict[str, str]]:
    return [_run(имя) for имя in sorted(ЭТАЛОН)]


# ── эталон ────────────────────────────────────────────────────────────────


def test_пустой_эталон_это_отказ_а_не_зелено() -> None:
    """Без эталона судить не по чему, и «нет красных» выполняется на пустоте."""
    итог = pr_check.verdict(_все_зелёные(), frozenset())

    assert not итог.ok
    assert not итог.wait
    assert "эталонного набора" in итог.problems[0]


def test_эталон_из_одного_себя_это_отказ() -> None:
    """Себя проверка вычитает — и остаётся ни с чем, то есть без эталона."""
    итог = pr_check.verdict(_все_зелёные(), frozenset({pr_check.SELF_NAME}))

    assert not итог.ok
    assert not итог.wait


# ── чтение проверок ───────────────────────────────────────────────────────


def test_проверок_нет_вовсе_это_ждать_а_не_зелено() -> None:
    """Сразу после пуша check-runs ещё не созданы, и пустота обманывает.

    «Нет красных, нет ожидающих» на пустом списке выполняется идеально.
    """
    итог = pr_check.verdict([], ЭТАЛОН)

    assert not итог.ok
    assert итог.wait
    assert "CI не стартовал" in итог.problems[0]


def test_неполный_набор_это_ждать() -> None:
    """Один джоб создан, остальных нет — это тоже «CI не стартовал»."""
    итог = pr_check.verdict([_run("гейты · ubuntu-latest · python 3.12")], ЭТАЛОН)

    assert not итог.ok
    assert итог.wait
    assert "джобы не созданы" in итог.problems[0]
    assert "зона, тип, связь с задачей и имя ветки" in итог.problems[0]


def test_гейт_только_для_pr_входит_в_эталон() -> None:
    """Разметки и changelog нет на общей ветке, и без них эталон был бы неполон.

    Не попади они в эталон — `PR check` позеленел бы раньше, чем эти гейты
    вообще стартовали.
    """
    assert pr_check.PR_ONLY_CHECKS <= ЭТАЛОН

    гейт = "зона, тип, связь с задачей и имя ветки"
    без_разметки = [c for c in _все_зелёные() if c["name"] != гейт]
    итог = pr_check.verdict(без_разметки, ЭТАЛОН)

    assert not итог.ok
    assert итог.wait


def test_идущая_проверка_это_ждать() -> None:
    проверки = _все_зелёные()
    проверки[0] = _run(проверки[0]["name"], status="in_progress", conclusion="")

    итог = pr_check.verdict(проверки, ЭТАЛОН)

    assert not итог.ok
    assert итог.wait
    assert "ещё идут" in итог.problems[0]


def test_красная_проверка_это_отказ_а_не_ожидание() -> None:
    """Красное не рассосётся: ждать на нём значило бы ждать до таймаута."""
    проверки = _все_зелёные()
    проверки[0] = _run(проверки[0]["name"], conclusion="failure")

    итог = pr_check.verdict(проверки, ЭТАЛОН)

    assert not итог.ok
    assert not итог.wait
    assert "не зелёные" in итог.problems[0]


def test_отменённая_проверка_считается_красной() -> None:
    """`cancelled` — не «зелено» и не «пропущено»: результата нет."""
    проверки = _все_зелёные()
    проверки[0] = _run(проверки[0]["name"], conclusion="cancelled")

    итог = pr_check.verdict(проверки, ЭТАЛОН)

    assert not итог.ok
    assert not итог.wait


def test_пропущенное_и_нейтральное_зелёные() -> None:
    проверки = [
        _run(имя, conclusion="skipped" if "гейты" in имя else "neutral")
        for имя in sorted(ЭТАЛОН)
    ]

    assert pr_check.verdict(проверки, ЭТАЛОН).ok


def test_всё_зелено() -> None:
    итог = pr_check.verdict(_все_зелёные(), ЭТАЛОН)

    assert итог.ok
    assert итог.counted == len(ЭТАЛОН)


def test_себя_не_ждёт() -> None:
    """Пока `PR check` считает, он сам числится идущим на той же голове.

    Учти он себя — не завершился бы никогда: ждал бы собственного результата.
    """
    проверки = [
        _run(pr_check.SELF_NAME, status="in_progress", conclusion=""),
        *_все_зелёные(),
    ]

    итог = pr_check.verdict(проверки, ЭТАЛОН | {pr_check.SELF_NAME})

    assert итог.ok
    assert итог.counted == len(ЭТАЛОН)


def test_старый_комплект_не_воскрешает_красное() -> None:
    """После обновления ветки на коммите остаётся первый комплект check-runs.

    Площадка отдаёт список от свежего к старому, и побеждать обязан первый.
    """
    свежие = _все_зелёные()
    старые = [_run(c["name"], conclusion="failure") for c in свежие]

    итог = pr_check.verdict([*свежие, *старые], ЭТАЛОН)

    assert итог.ok
    assert итог.counted == len(ЭТАЛОН), "проверки посчитаны не по уникальным именам"


# ── ожидание ──────────────────────────────────────────────────────────────


def test_ждёт_и_дожидается() -> None:
    ответы = [[], [_run("гейты · ubuntu-latest · python 3.12")], _все_зелёные()]
    паузы: list[float] = []

    итог = pr_check.run(
        lambda: ответы.pop(0), ЭТАЛОН, attempts=5, interval=7, sleep=паузы.append
    )

    assert итог.ok
    assert паузы == [7, 7]


def test_не_ждёт_вечно() -> None:
    """Не дождавшись, проверка краснеет, а не висит.

    Красное — безопасная сторона: оно запрещает мерж, зелёное разрешило бы.
    """
    итог = pr_check.run(list, ЭТАЛОН, attempts=3, interval=1, sleep=lambda _: None)

    assert not итог.ok
    assert not итог.wait
    assert any("не дождались" in п for п in итог.problems)


def test_красное_прерывает_ожидание_сразу() -> None:
    """Ждать на красном значило бы жечь минуты до таймаута ради того же ответа."""
    обращений = 0

    def fetch() -> list[dict[str, str]]:
        nonlocal обращений
        обращений += 1
        проверки = _все_зелёные()
        проверки[0] = _run(проверки[0]["name"], conclusion="failure")
        return проверки

    итог = pr_check.run(fetch, ЭТАЛОН, attempts=9, interval=1, sleep=lambda _: None)

    assert not итог.ok
    assert обращений == 1


# ── список не разошёлся с деревом ─────────────────────────────────────────


def _джобы(путь: Path) -> set[str]:
    """Имена джобов workflow: строка `name:` с отступом ровно в четыре пробела.

    У шагов отступ больше и есть дефис, у workflow — меньше.
    """
    return set(re.findall(r"^    name: (.+)$", путь.read_text(encoding="utf-8"), re.M))


def _workflow_по_pull_request() -> list[Path]:
    """Файлы, чьи джобы создаются на PR и **не** создаются на общей ветке."""
    найденные = []
    for путь in sorted(WORKFLOWS.glob("*.yml")):
        if путь.name in {"ci.yml", "pr-check.yml"}:
            continue
        if re.search(r"^  pull_request:", путь.read_text(encoding="utf-8"), re.M):
            найденные.append(путь)
    return найденные


def test_pr_only_checks_совпадают_с_деревом() -> None:
    """Список гейтов «только для PR» назван дважды и разошёлся бы молча.

    Расхождение опасно в обе стороны: лишнее имя — вечное ожидание джоба,
    которого никто не создаёт; недостающее — `PR check` зеленеет, не дождавшись
    гейта, который ещё и не стартовал.
    """
    файлы = _workflow_по_pull_request()
    assert файлы, "не нашлось ни одного workflow, ходящего по pull_request"

    имена: set[str] = set()
    for путь in файлы:
        джобы = _джобы(путь)
        assert джобы, f"{путь.name}: у джоба нет имени, а имя — это check-run"
        имена |= джобы

    assert имена == set(pr_check.PR_ONLY_CHECKS)


def test_имя_джоба_совпадает_с_обязательным_контекстом() -> None:
    """Переименование джоба равносильно снятию защиты с `main`.

    Ruleset требует контекст `PR check` дословно. Разойдись имя джоба с ним —
    и ни один PR не станет мержимым: площадка будет ждать проверку, которой
    никто не создаёт.
    """
    assert _джобы(WORKFLOWS / "pr-check.yml") == {pr_check.SELF_NAME}
