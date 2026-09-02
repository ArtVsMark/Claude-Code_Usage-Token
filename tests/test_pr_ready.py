"""Проверки вердикта «можно ли мержить» (#8).

Здесь у ложного «прошло» цена выше, чем где-либо ещё в проекте: оно означает
смерженный PR. Поэтому каждое правило чтения проверок закреплено тестом на
**отказ**, а рядом — тест на законный случай, чтобы починка одной стороны не
завернула другую.
"""

from __future__ import annotations

from typing import Any

import pr_check
import pr_ready

#: Проверки на голове PR. Обязательная `PR check` среди них: очередь требует
#: именно её — она и отвечает за полноту набора (#46).
НАБОР = frozenset(
    {"гейты · ubuntu-latest · python 3.12", "зона, тип и связь", pr_check.SELF_NAME}
)


def _check(
    имя: str, вывод: str = "success", статус: str = "completed"
) -> dict[str, Any]:
    return {"name": имя, "status": статус, "conclusion": вывод}


def _зелёные() -> list[dict[str, Any]]:
    return [_check(имя) for имя in sorted(НАБОР)]


def _pull(**поля: Any) -> dict[str, Any]:
    основа: dict[str, Any] = {
        "number": 42,
        "state": "open",
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "body": "Closes #1",
        "labels": [{"name": "area/ci"}, {"name": "enhancement"}],
        "head": {"sha": "deadbeef", "ref": "agent/пример", "repo": {"fork": False}},
    }
    основа.update(поля)
    return основа


def _снимок(**поля: Any) -> pr_ready.Snapshot:
    параметры: dict[str, Any] = {
        "pull": _pull(),
        "checks": _зелёные(),
        "main_busy": False,
        "main_red": False,
    }
    параметры.update(поля)
    return pr_ready.Snapshot(**параметры)


# ── законное едет ─────────────────────────────────────────────────────────


def test_зелёный_размеченный_pr_готов() -> None:
    вердикт = pr_ready.evaluate(_снимок())
    assert вердикт.state == pr_ready.READY
    assert "проверок по уникальным именам 3" in вердикт.reasons[0]


def test_neutral_и_skipped_не_считаются_красными() -> None:
    """Пропущенный джоб — законный исход, а не поломка."""
    проверки = [
        _check("гейты · ubuntu-latest · python 3.12", "skipped"),
        _check("зона, тип и связь", "neutral"),
        _check(pr_check.SELF_NAME, "neutral"),
    ]
    assert pr_ready.evaluate(_снимок(checks=проверки)).ready


def test_второй_комплект_проверок_не_воскрешает_вчерашнее_красное() -> None:
    """Считаем по уникальным именам, а не по строкам.

    После обновления ветки площадка создаёт второй комплект check-runs, а
    первый остаётся на коммите. На этом уже был неверный вывод — «32 проверки»
    вместо шестнадцати, продержавшийся сутки. Здесь важнее другое: старый
    красный run с тем же именем не должен перевесить новый зелёный.
    """
    свежие = _зелёные()
    устаревшие = [_check(имя, "failure") for имя in sorted(НАБОР)]

    вердикт = pr_ready.evaluate(_снимок(checks=свежие + устаревшие))

    assert вердикт.state == pr_ready.READY
    assert "уникальным именам 3" in вердикт.reasons[0]


# ── три правила чтения проверок ───────────────────────────────────────────


def test_пустой_список_проверок_не_зелено() -> None:
    """Главное правило: пустота — это «CI не стартовал», а не «зелено».

    Наивная проверка «нет красных и нет ожидающих» выполняется на пустом
    списке идеально — и ровно так в соседнем проекте смержили PR, у которого
    9 проверок из 14 ещё стояли в очереди.
    """
    вердикт = pr_ready.evaluate(_снимок(checks=[]))
    assert вердикт.state == pr_ready.WAIT
    assert "CI не стартовал" in вердикт.reasons[0]


def test_конфликт_не_ждут_а_метят() -> None:
    """Конфликт — это «проверок нет вовсе», а не «CI сломался».

    Прогон на PR идёт по merge-коммиту: слияние невозможно — проверки не
    создаются. Очередь, ожидающая на таком PR, стоит вся: у соседа три падения
    подряд, 14 часов простоя и четыре здоровых PR рядом.
    """
    вердикт = pr_ready.evaluate(_снимок(pull=_pull(mergeable_state="dirty")))
    assert вердикт.state == pr_ready.CONFLICT

    вердикт = pr_ready.evaluate(_снимок(pull=_pull(mergeable=False)))
    assert вердикт.state == pr_ready.CONFLICT


def test_отставшая_ветка_не_готова() -> None:
    """«Зелено на моей ветке» ≠ «зелено после мержа»."""
    вердикт = pr_ready.evaluate(_снимок(pull=_pull(mergeable_state="behind")))
    assert вердикт.state == pr_ready.STALE
    assert "которого после мержа не будет" in вердикт.reasons[0]


def test_отставание_видно_и_без_защиты_ветки() -> None:
    """Дефект, найденный на живых данных, а не на подделанных.

    Проверка отставания опиралась на `mergeable_state == "behind"`, а площадка
    выставляет это значение **только при включённой защите ветки** с
    требованием актуальности. Без защиты отставший PR приходит как `clean`:
    PR #32 с базой 70bd5be при `main` на d4ec6a7 отдавал именно `clean`, хотя
    сравнение показывало `behind_by: 1`.

    Гейт при этом выглядел рабочим — его вход просто никогда не краснел.
    """
    вердикт = pr_ready.evaluate(
        _снимок(pull=_pull(mergeable_state="clean"), behind_by=1)
    )

    assert вердикт.state == pr_ready.STALE
    assert "на 1 коммит" in вердикт.reasons[0]


def test_неотставшая_ветка_не_считается_отставшей() -> None:
    """Обратная сторона: выдумывать отставание значит остановить очередь зря."""
    assert pr_ready.evaluate(_снимок(behind_by=0)).ready


def test_без_обязательной_проверки_это_ждать() -> None:
    """`PR check` отвечает за полноту набора, поэтому её отсутствие — ожидание.

    Эталона имён с общей ветки здесь больше нет: он ломал любой PR, который
    состав проверок меняет (#46).
    """
    без_неё = [c for c in _зелёные() if c["name"] != pr_check.SELF_NAME]

    вердикт = pr_ready.evaluate(_снимок(checks=без_неё))

    assert вердикт.state == pr_ready.WAIT
    assert pr_check.SELF_NAME in вердикт.reasons[0]


def test_идущие_проверки_ждут() -> None:
    проверки = [
        _check("гейты · ubuntu-latest · python 3.12"),
        _check(pr_check.SELF_NAME),
        _check("зона, тип и связь", "", "in_progress"),
    ]
    вердикт = pr_ready.evaluate(_снимок(checks=проверки))
    assert вердикт.state == pr_ready.WAIT
    assert "ещё идут" in вердикт.reasons[0]


def test_красная_проверка_блокирует() -> None:
    проверки = [
        _check("гейты · ubuntu-latest · python 3.12"),
        _check(pr_check.SELF_NAME),
        _check("зона, тип и связь", "failure"),
    ]
    вердикт = pr_ready.evaluate(_снимок(checks=проверки))
    assert вердикт.state == pr_ready.BLOCKED
    assert "не зелёные" in вердикт.reasons[0]


def test_hold_сильнее_зелёного() -> None:
    """Стоп-метка сильнее всего: это решение, а не состояние."""
    pull = _pull(
        labels=[{"name": "area/ci"}, {"name": "enhancement"}, {"name": pr_ready.HOLD}]
    )
    вердикт = pr_ready.evaluate(_снимок(pull=pull))
    assert вердикт.state == pr_ready.HELD


def test_черновик_не_едет() -> None:
    assert pr_ready.evaluate(_снимок(pull=_pull(draft=True))).state == pr_ready.BLOCKED


def test_закрытый_pr_не_едет() -> None:
    assert (
        pr_ready.evaluate(_снимок(pull=_pull(state="closed"))).state == pr_ready.BLOCKED
    )


def test_неразмеченный_pr_не_едет() -> None:
    """Очередь не мержит то, что завернул бы гейт разметки.

    Проверка разметки на PR не обязательная — защиты ветки нет, — и полагаться
    на неё очередь не может: она обязана спросить сама.
    """
    вердикт = pr_ready.evaluate(_снимок(pull=_pull(labels=[], body="")))
    assert вердикт.state == pr_ready.BLOCKED
    assert "разметка:" in вердикт.reasons[0]


def test_слияние_ещё_считается() -> None:
    """`mergeable: null` — «площадка не досчитала», а не «конфликта нет»."""
    вердикт = pr_ready.evaluate(_снимок(pull=_pull(mergeable=None)))
    assert вердикт.state == pr_ready.WAIT


def test_красный_main_держит_очередь() -> None:
    вердикт = pr_ready.evaluate(_снимок(main_red=True))
    assert вердикт.state == pr_ready.WAIT
    assert "красный" in вердикт.reasons[0]


def test_идущий_прогон_на_main_держит_очередь() -> None:
    """Мерж внахлёст вытесняет ожидающий прогон, и тот не начинается вовсе."""
    вердикт = pr_ready.evaluate(_снимок(main_busy=True))
    assert вердикт.state == pr_ready.WAIT
    assert "внахлёст" in вердикт.reasons[0]


def test_конфликт_проверяется_раньше_проверок() -> None:
    """Порядок важен: на конфликтном PR проверок не существует.

    Спроси мы сначала про них, вердикт был бы «ждать зелёного» — то есть
    ожидание того, что не появится никогда.
    """
    вердикт = pr_ready.evaluate(_снимок(pull=_pull(mergeable_state="dirty"), checks=[]))
    assert вердикт.state == pr_ready.CONFLICT
