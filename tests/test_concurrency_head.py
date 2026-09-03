"""Проверки гейта на группу отмены без головы (#88).

Дефект, ради которого гейт заведён, не виден в дифффе и не воспроизводится по
желанию: он требует гонки событий. Поэтому почти все проверки здесь — про
**границы**, то есть про случаи, когда гейт обязан промолчать. Гейт, который
краснеет на законном, выключают первой же правкой вместе со всем остальным.
"""

from __future__ import annotations

from pathlib import Path

import concurrency_head

КОРЕНЬ = Path(__file__).resolve().parents[1]

ШАПКА = "name: пример\non:\n  pull_request:\n    types:\n      - opened\n"
ХВОСТ = "jobs:\n  x:\n    steps:\n      - run: echo ok\n"


def _находки(текст: str) -> list[str]:
    return [н.message for н in concurrency_head.check_text(текст, "w.yml")]


def test_группа_без_головы_краснеет() -> None:
    текст = (
        ШАПКА + "concurrency:\n"
        "  group: pr-check-${{ github.event.pull_request.number }}\n"
        "  cancel-in-progress: true\n" + ХВОСТ
    )
    находки = _находки(текст)
    assert len(находки) == 1
    assert "не называет голову" in находки[0]


def test_группа_с_головой_чиста() -> None:
    текст = (
        ШАПКА + "concurrency:\n"
        "  group: pr-check-${{ github.event.pull_request.number }}"
        "-${{ github.event.pull_request.head.sha }}\n"
        "  cancel-in-progress: true\n" + ХВОСТ
    )
    assert _находки(текст) == []


def test_запасной_вариант_головы_годится() -> None:
    """`head.sha || github.sha` — та же голова, просто с запасом для push."""
    текст = (
        ШАПКА + "concurrency:\n"
        "  group: ci-${{ github.ref }}"
        "-${{ github.event.pull_request.head.sha || github.sha }}\n"
        "  cancel-in-progress: ${{ github.ref != 'refs/heads/main' }}\n" + ХВОСТ
    )
    assert _находки(текст) == []


def test_отмены_нет_голова_не_нужна() -> None:
    """`cancel-in-progress: false` — вытеснять нечем, и требовать нечего."""
    текст = (
        ШАПКА + "concurrency:\n"
        "  group: pr-check-${{ github.event.pull_request.number }}\n"
        "  cancel-in-progress: false\n" + ХВОСТ
    )
    assert _находки(текст) == []


def test_отмена_не_задана_умолчание_площадки_не_отменяет() -> None:
    текст = (
        ШАПКА + "concurrency:\n"
        "  group: pr-check-${{ github.event.pull_request.number }}\n" + ХВОСТ
    )
    assert _находки(текст) == []


def test_блока_concurrency_нет_вовсе() -> None:
    assert _находки(ШАПКА + ХВОСТ) == []


def test_отмена_без_группы_краснеет() -> None:
    """Группа по умолчанию непредсказуема — это хуже, чем группа без головы."""
    текст = ШАПКА + "concurrency:\n  cancel-in-progress: true\n" + ХВОСТ
    находки = _находки(текст)
    assert len(находки) == 1
    assert "group — нет" in находки[0]


def test_не_pull_request_не_проверяется() -> None:
    """У прогона по расписанию головы PR нет, и требовать её бессмысленно."""
    текст = (
        "name: по расписанию\n"
        "on:\n"
        "  schedule:\n"
        "    - cron: '0 * * * *'\n"
        "concurrency:\n"
        "  group: nightly\n"
        "  cancel-in-progress: true\n" + ХВОСТ
    )
    assert _находки(текст) == []
    assert concurrency_head.listens_to_pr(текст) is False


def test_on_в_кавычках_разбирается() -> None:
    """YAML 1.1 читает голое `on` как булево, и осторожные пишут `"on":`."""
    текст = (
        'name: пример\n"on":\n  pull_request:\n'
        "concurrency:\n"
        "  group: x-${{ github.event.pull_request.number }}\n"
        "  cancel-in-progress: true\n" + ХВОСТ
    )
    assert concurrency_head.listens_to_pr(текст) is True
    assert len(_находки(текст)) == 1


def test_однострочная_форма_события() -> None:
    текст = (
        "name: пример\non: [pull_request]\n"
        "concurrency:\n"
        "  group: x-${{ github.event.pull_request.number }}\n"
        "  cancel-in-progress: true\n" + ХВОСТ
    )
    assert concurrency_head.listens_to_pr(текст) is True
    assert len(_находки(текст)) == 1


def test_pull_request_target_тоже_предмет() -> None:
    текст = (
        "name: пример\non:\n  pull_request_target:\n"
        "concurrency:\n"
        "  group: x-${{ github.event.pull_request.number }}\n"
        "  cancel-in-progress: true\n" + ХВОСТ
    )
    assert len(_находки(текст)) == 1


def test_блок_кончается_на_следующем_ключе() -> None:
    """`group:` из чужого блока не должен приниматься за свой.

    Ключ `group` встречается и внутри `jobs:` — например у `strategy`. Если
    тело блока не обрывать на первой строке без отступа, гейт прочитал бы
    чужое значение и промолчал бы на настоящем нарушении.
    """
    текст = (
        ШАПКА + "concurrency:\n"
        "  group: pr-check-${{ github.event.pull_request.number }}\n"
        "  cancel-in-progress: true\n"
        "jobs:\n"
        "  x:\n"
        "    group: ${{ github.event.pull_request.head.sha }}\n"
        "    steps:\n"
        "      - run: echo ok\n"
    )
    находки = _находки(текст)
    assert len(находки) == 1, "значение из jobs: приняли за своё"


def test_комментарий_не_считается_ключом() -> None:
    текст = (
        ШАПКА + "concurrency:\n"
        "  # group: ${{ github.event.pull_request.head.sha }}\n"
        "  group: pr-check-${{ github.event.pull_request.number }}\n"
        "  cancel-in-progress: true\n" + ХВОСТ
    )
    assert len(_находки(текст)) == 1


def test_дерево_без_предмета_отказывает() -> None:
    """«Предмета нет» — утверждение о действительности, и оно говорится вслух."""
    находки = concurrency_head.check_workflows(Path("/несуществующий-корень"))
    assert len(находки) == 1
    assert "без предмета" in находки[0].message


def test_проект_чист() -> None:
    """Гейт на самом себе: workflow проекта не должны краснеть."""
    assert concurrency_head.check_workflows(КОРЕНЬ) == []
