"""Ответ каталогу правил разрешим, а не правдоподобен (#47).

Проверяется форма ответа, а не его правдивость: «держится гейтом» — утверждение
о смысле, и подтвердить его может только человек. Гейт отвечает на вопрос «назван
ли адрес и существует ли он».
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import rules_answer

КОРЕНЬ = Path(__file__).resolve().parents[1]


def _ответ(**правила: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema": rules_answer.СХЕМА_ОТВЕТА,
        "project": "Владелец/Проект",
        "rules": правила,
    }


def _сообщения(данные: dict[str, Any], *, root: Path = КОРЕНЬ) -> list[str]:
    return [н.message for н in rules_answer.check_answer(данные, root=root).находки]


def test_живой_ответ_проекта_разрешим() -> None:
    """Не подделка: настоящий `.rules/bindings.json`.

    Замер 2026-09-03 до правки — 17 негодных записей: устаревший `process-step`,
    причина отказа не в том поле, проза вместо адреса и адреса, не
    разрешающиеся в дереве.
    """
    результат = rules_answer.check_tree(КОРЕНЬ)

    assert результат.находки == []
    assert результат.записей > 0, "гейт обязан назвать охват, а не только «чисто»"
    assert результат.с_адресом > 0


def test_проза_вместо_адреса_это_отказ() -> None:
    """Гейт, чей адрес нельзя назвать, обычно и не гейт.

    Ловится не форма записи, а ложный механизм: у каталога замер по
    собственному ответу дал семь таких, и все семь оказались утверждениями обо
    всех скриптах сразу, которых не проверяет ничто.
    """
    сообщения = _сообщения(
        _ответ(
            **{
                "001": {
                    "status": "active",
                    "mechanism": "gate",
                    "where": "держится тестами",
                }
            }
        )
    )

    assert len(сообщения) == 1
    assert "не называет ни одного адреса" in сообщения[0]


def test_мёртвый_адрес_это_отказ() -> None:
    """Адрес бывает разрешимым по форме и мёртвым по сути.

    Переименование переживает такой ответ молча: он остаётся правдоподобным.
    Этой проверки в контракте каталога нет — она своя.
    """
    сообщения = _сообщения(
        _ответ(
            **{
                "001": {
                    "status": "active",
                    "mechanism": "gate",
                    "where": "scripts/нет_такого_файла.py — держит вот это",
                }
            }
        )
    )

    assert len(сообщения) == 1
    assert "не разрешается" in сообщения[0]


def test_живой_адрес_молчит() -> None:
    данные = _ответ(
        **{
            "001": {
                "status": "active",
                "mechanism": "gate",
                "where": "scripts/preflight.py — проза рядом с адресом разрешена",
            }
        }
    )
    assert _сообщения(данные) == []


def test_образец_пути_считается_адресом() -> None:
    """Контракт разрешает образец вида `.github/workflows/*.yml`."""
    данные = _ответ(
        **{
            "001": {
                "status": "active",
                "mechanism": "gate",
                "where": ".github/workflows/*.yml — кнопка есть у каждого",
            }
        }
    )
    assert _сообщения(данные) == []


def test_устаревший_механизм_это_отказ() -> None:
    """`process-step` контракт объявил устаревшим.

    Оставленный, он однажды перестанет читаться на той стороне, а здесь будет
    выглядеть ответом.
    """
    сообщения = _сообщения(
        _ответ(
            **{
                "001": {
                    "status": "active",
                    "mechanism": "process-step",
                    "where": "scripts/preflight.py",
                }
            }
        )
    )

    assert len(сообщения) == 1
    assert "устаревшим" in сообщения[0]


def test_отказ_без_причины_это_не_ответ() -> None:
    """У `rejected` и `not-applicable` причина обязательна — и именно в `why`.

    Причина, записанная в `where`, читается человеком и теряется механизмом:
    контракт ищет её в своём поле.
    """
    for статус in ("rejected", "not-applicable"):
        сообщения = _сообщения(
            _ответ(**{"001": {"status": статус, "mechanism": "none"}})
        )
        assert len(сообщения) == 1
        assert "без причины" in сообщения[0]


def test_none_обязан_сказать_чем_держится() -> None:
    """`active` с `mechanism=none` законен, но молчание вместо объяснения — нет."""
    сообщения = _сообщения(
        _ответ(**{"048": {"status": "active", "mechanism": "none", "where": ""}})
    )

    assert len(сообщения) == 1
    assert "чем держится" in сообщения[0]


def test_чужая_версия_схемы_это_отказ() -> None:
    """Три разных числа зовутся ключом `schema`, и наш файл был примером (164)."""
    данные = _ответ(**{"001": {"status": "unreviewed"}})
    данные["schema"] = "1.2"

    сообщения = _сообщения(данные)

    assert len(сообщения) == 1
    assert "ФОРМАТА ОТВЕТА" in сообщения[0]


def test_unreviewed_не_требует_ничего() -> None:
    """«Не дошли руки» — честный статус, пока он не застаивается."""
    assert _сообщения(_ответ(**{"001": {"status": "unreviewed"}})) == []


def test_отсутствие_файла_это_не_чисто(tmp_path: Path) -> None:
    """Молчание вместо ответа неотличимо от согласия со всеми правилами сразу."""
    результат = rules_answer.check_tree(tmp_path)

    assert результат.находки != []
    assert "ответа каталогу нет" in результат.находки[0].message


def test_гейт_отдаёт_ненулевой_код(tmp_path: Path) -> None:
    """Гейт, который нельзя провалить, — не гейт (правило 075)."""
    (tmp_path / ".rules").mkdir()
    (tmp_path / ".rules" / "bindings.json").write_text(
        json.dumps(
            _ответ(
                **{"001": {"status": "active", "mechanism": "gate", "where": "проза"}}
            ),
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    ответ = subprocess.run(
        [
            sys.executable,
            str(КОРЕНЬ / "scripts" / "rules_answer.py"),
            "--root",
            str(tmp_path),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    assert ответ.returncode == rules_answer.EXIT_FAILED
    assert "правило 001" in ответ.stdout
