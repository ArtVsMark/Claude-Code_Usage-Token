"""Цена прогонов по расписанию (правило 033 каталога).

Дороже всего здесь **незаявленный прогон**: расписание добавляют одной строкой,
и посчитать его цену не догадается никто — квота выжигается фоном, а отказ
приходит соседнему прогону, не этому.

Второе по цене — **непонятое расписание**. Молчаливый пропуск дал бы заниженное
число стартов, то есть заниженную цену: ошибку ровно в ту сторону, ради которой
гейт заведён. Поэтому неразобранный cron — отказ, а не ноль.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import check_schedules

КОРЕНЬ = Path(__file__).resolve().parents[1]


def _дерево(
    tmp_path: Path,
    расписания: list[dict[str, object]],
    *,
    прогоны: dict[str, str] | None = None,
    limit: int = 5000,
    share: float = 0.1,
) -> Path:
    (tmp_path / ".rules").mkdir(parents=True, exist_ok=True)
    (tmp_path / check_schedules.WORKFLOWS).mkdir(parents=True, exist_ok=True)
    for имя, текст in (прогоны or {}).items():
        (tmp_path / check_schedules.WORKFLOWS / имя).write_text(текст, encoding="utf-8")
    (tmp_path / check_schedules.ПЕРЕПИСЬ).write_text(
        json.dumps(
            {
                "schema": check_schedules.СХЕМА,
                "hourly_limit": limit,
                "budget_share": share,
                "schedules": расписания,
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return tmp_path


ПРОГОН = (
    'on:\n  schedule:\n    - cron: "0 * * * *"\n'
    "jobs:\n  x:\n    runs-on: ubuntu-latest\n"
)


def _запись(имя: str, cron: str, цена: int) -> dict[str, object]:
    return {
        "workflow": f".github/workflows/{имя}",
        "cron": cron,
        "calls_per_run": цена,
        "why": "проверочная запись",
    }


# ── разбор расписания ───────────────────────────────────────────────────────


@pytest.mark.parametrize(
    ("cron", "ждём"),
    [
        ("0 * * * *", {ч: 1 for ч in range(24)}),
        ("13,43 * * * *", {ч: 2 for ч in range(24)}),
        ("17 6 * * *", {6: 1}),
        ("0 */6 * * *", {0: 1, 6: 1, 12: 1, 18: 1}),
        ("0,30 9-11 * * *", {9: 2, 10: 2, 11: 2}),
    ],
)
def test_стартов_по_часам(cron: str, ждём: dict[int, int]) -> None:
    assert check_schedules.стартов_по_часам(cron) == ждём


@pytest.mark.parametrize("cron", ["не cron", "* * * *", "0 25 * * *", "0 */0 * * *"])
def test_непонятое_расписание_это_отказ(cron: str) -> None:
    """Заниженное число стартов — ошибка в ту сторону, ради которой гейт заведён."""
    assert check_schedules.стартов_по_часам(cron) is None


def test_дни_недели_не_снижают_худший_час() -> None:
    """Считается ХУДШИЙ час; ограничение по дням его только уменьшает."""
    assert check_schedules.стартов_по_часам("0 8 * * 1-5") == {8: 1}


# ── полнота переписи ────────────────────────────────────────────────────────


def test_незаявленный_прогон_это_отказ(tmp_path: Path) -> None:
    """Самая дешёвая ошибка: расписание добавили строкой, цену не назвал никто."""
    корень = _дерево(tmp_path, [], прогоны={"новый.yml": ПРОГОН})
    находки = check_schedules.проверить(корень).находки
    assert len(находки) == 1
    assert "цена его не объявлена" in находки[0]


def test_прогон_без_расписания_не_требуется(tmp_path: Path) -> None:
    """Предмет правила — расписания, а не все прогоны подряд."""
    корень = _дерево(
        tmp_path, [], прогоны={"по-толчку.yml": "on:\n  push:\n    branches: [main]\n"}
    )
    assert check_schedules.проверить(корень).находки == []


def test_объявленного_прогона_нет_в_дереве(tmp_path: Path) -> None:
    корень = _дерево(tmp_path, [_запись("пропал.yml", "0 * * * *", 10)])
    находки = check_schedules.проверить(корень).находки
    assert len(находки) == 1
    assert "в дереве нет" in находки[0]


def test_число_без_причины_не_объявленная_цена(tmp_path: Path) -> None:
    корень = _дерево(
        tmp_path,
        [
            {
                "workflow": ".github/workflows/x.yml",
                "cron": "0 * * * *",
                "calls_per_run": 5,
            }
        ],
        прогоны={"x.yml": ПРОГОН},
    )
    находки = check_schedules.проверить(корень).находки
    assert any("why" in н for н in находки)


# ── арифметика бюджета ──────────────────────────────────────────────────────


def test_худший_час_суммирует_расписания(tmp_path: Path) -> None:
    """Два прогона в один час складываются — это и есть предмет счёта."""
    корень = _дерево(
        tmp_path,
        [_запись("a.yml", "0 6 * * *", 100), _запись("b.yml", "30 6 * * *", 50)],
        прогоны={"a.yml": ПРОГОН, "b.yml": ПРОГОН},
    )
    итог = check_schedules.проверить(корень)
    assert итог.худший_час == 150
    assert итог.находки == []


def test_перебор_бюджета_это_отказ(tmp_path: Path) -> None:
    корень = _дерево(
        tmp_path, [_запись("a.yml", "*/1 * * * *", 20)], прогоны={"a.yml": ПРОГОН}
    )
    итог = check_schedules.проверить(корень)
    assert итог.худший_час == 60 * 20
    assert any("худший час" in н for н in итог.находки)


# ── своё дерево ─────────────────────────────────────────────────────────────


def test_свои_расписания_укладываются_в_бюджет() -> None:
    итог = check_schedules.проверить(КОРЕНЬ)
    assert итог.находки == []
    assert 0 < итог.худший_час <= итог.бюджет


def test_код_возврата_меняется_находкой(tmp_path: Path) -> None:
    _дерево(tmp_path, [], прогоны={"новый.yml": ПРОГОН})
    assert check_schedules.main([str(tmp_path)]) == check_schedules.EXIT_FAILED
    assert check_schedules.main([str(КОРЕНЬ)]) == 0


def test_переписи_нет_это_воздержание(tmp_path: Path) -> None:
    """Файла нет — считать нечего, и это не то же, что пустая перепись (039).

    Тихого разоружения не даёт: адрес переписи назван в ответе каталогу, а
    гейт ответа отвергает адрес, которого в дереве нет.
    """
    итог = check_schedules.проверить(tmp_path)
    assert итог.находки == []
    assert итог.переписи_нет is True
    assert check_schedules.main([str(tmp_path)]) == 0
