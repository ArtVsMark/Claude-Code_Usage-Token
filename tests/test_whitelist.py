"""Проверки белого списка полей замера (#7).

Замер уходит в git навсегда, и обе ошибки здесь необратимы по-разному.

**Ложное «прошло»** — в замер попало лишнее. Публичный git помнит удалённое, а
приватный лишь уменьшает ущерб; отозвать записанное нельзя.

**Ложное «не прошло»** — законная запись реестра завёрнута, замеры не пишутся,
и в накопленных данных появляется дыра. Дыру тоже не дописать: реестр отдаёт
то, что знает сейчас.

Поэтому проверяется и то, и другое — на подделанном реестре.
"""

from __future__ import annotations

from typing import Any

import pytest

from claude_code_usage import whitelist


def _сессия(**правки: Any) -> dict[str, Any]:
    """Запись реестра в том виде, в каком её описывает docs/spec.md."""
    запись: dict[str, Any] = {
        "usage": {
            "cache_read_tokens": 89595066,
            "cache_write_tokens": 1104683,
            "input_tokens": 61369,
            "output_tokens": 182703,
            "cost_usd": 106.66,
        },
        "rate_limit_info": {
            "rateLimitType": "seven_day",
            "resetsAt": 1787731200,
            "status": "allowed_warning",
            "isUsingOverage": False,
        },
    }
    запись.update(правки)
    return запись


def _замер(**правки: Any) -> dict[str, Any]:
    return whitelist.build_sample(
        _сессия(**правки), ts="2026-08-31T09:00:00Z", session_id="s1"
    )


# ── законное проходит ─────────────────────────────────────────────────────


def test_замер_собирается_из_пяти_чисел() -> None:
    """Пять слагаемых расхода, а не три — решение по #18."""
    замер = _замер()

    assert замер["cache_read"] == 89595066
    assert замер["cache_write"] == 1104683
    assert замер["input"] == 61369
    assert замер["output"] == 182703
    assert замер["cost_usd"] == 106.66


def test_состояние_светофора_попадает() -> None:
    замер = _замер()
    assert (замер["type"], замер["status"], замер["resets_at"]) == (
        "seven_day",
        "allowed_warning",
        1787731200,
    )


def test_замер_чист_по_составу() -> None:
    assert whitelist.audit(_замер()) == []


# ── незнакомое не попадает ────────────────────────────────────────────────


def test_поле_вне_usage_отбрасывается_молча() -> None:
    """Метаданные сессии нас не касаются: они и не должны были попасть.

    Именно здесь белый список делает свою работу. Чёрный не пережил бы
    появления `authorization` в записи, потому что его в нём нет — а секрет
    утекает не через известное поле, а через незнакомое.
    """
    замер = _замер(
        authorization="Bearer sk-ant-секрет",
        title="Починить очередь мержей",
        cwd="/home/user/секретный-проект",
    )

    assert whitelist.audit(замер) == []
    строка = repr(замер)
    assert "Bearer" not in строка
    assert "Починить" not in строка
    assert "секретный-проект" not in строка


def test_isusingoverage_не_берётся() -> None:
    """Поле «на всякий случай» — то, что 🔐 Хранитель данных имеет право снять."""
    assert "isUsingOverage" not in _замер()
    assert not any("overage" in ключ.lower() for ключ in _замер())


# ── незнакомое слагаемое расхода — отказ ──────────────────────────────────


def test_незнакомое_поле_в_usage_роняет_замер() -> None:
    """Молчаливая фильтрация здесь дороже отказа.

    Новое поле внутри `usage` — скорее всего новое слагаемое расхода.
    Отфильтровав его молча, инструмент записал бы заниженную сумму, и шкала
    была бы неверной на неизвестную долю — тот же дефект, что в #18, только
    нанесённый себе. Хранилище append-only: дописать потом не выйдет.
    """
    сессия = _сессия()
    сессия["usage"]["reasoning_tokens"] = 4200

    with pytest.raises(whitelist.UnknownUsageFieldError) as отказ:
        whitelist.build_sample(сессия, ts="t", session_id="s")

    assert отказ.value.fields == ["reasoning_tokens"]
    assert "reasoning_tokens" in str(отказ.value)


def test_отказ_называет_все_незнакомые_поля() -> None:
    """Одно за прогон — значит правку списка придётся делать в несколько заходов."""
    сессия = _сессия()
    сессия["usage"]["reasoning_tokens"] = 1
    сессия["usage"]["tool_tokens"] = 2

    with pytest.raises(whitelist.UnknownUsageFieldError) as отказ:
        whitelist.build_sample(сессия, ts="t", session_id="s")

    assert отказ.value.fields == ["reasoning_tokens", "tool_tokens"]


def test_отказ_отдельного_типа() -> None:
    """«Реестр изменился» и «реестр отдал мусор» требуют разных действий."""
    сессия = _сессия()
    сессия["usage"]["новое"] = 1

    with pytest.raises(whitelist.UnknownUsageFieldError):
        whitelist.build_sample(сессия, ts="t", session_id="s")

    assert issubclass(whitelist.UnknownUsageFieldError, ValueError)


# ── мусор вместо реестра ──────────────────────────────────────────────────


def test_неполный_usage_роняет_замер() -> None:
    """Замер из неполной суммы занизит расход, а дописать потом не выйдет."""
    сессия = _сессия()
    del сессия["usage"]["cache_write_tokens"]

    with pytest.raises(ValueError, match="cache_write_tokens"):
        whitelist.build_sample(сессия, ts="t", session_id="s")


def test_записи_без_usage_роняют_замер() -> None:
    """Тихая пустая строка отравила бы шкалу и нашлась бы через недели."""
    with pytest.raises(ValueError, match="замерять нечего"):
        whitelist.build_sample({"rate_limit_info": {}}, ts="t", session_id="s")

    with pytest.raises(ValueError, match="замерять нечего"):
        whitelist.build_sample({"usage": {}}, ts="t", session_id="s")


def test_мусор_вместо_блоков_роняет_замер() -> None:
    with pytest.raises(ValueError, match="замерять нечего"):
        whitelist.build_sample(
            {"usage": "не объект", "rate_limit_info": {}}, ts="t", session_id="s"
        )


# ── проверка уже записанного ──────────────────────────────────────────────


def test_аудит_находит_лишнее_в_старой_строке() -> None:
    """Файл замеров переживает несколько версий инструмента.

    Строка, написанная старой версией, может нести поле, которого в белом
    списке больше нет, — и читатель обязан это увидеть, а не молча учесть.
    """
    строка = dict(_замер(), authorization="Bearer x", branch="claude/whitelist-7")
    assert whitelist.audit(строка) == ["authorization", "branch"]


def test_состав_замера_объявлен_целиком() -> None:
    """`SAMPLE_FIELDS` не должен разойтись с тем, что собирает `build_sample`."""
    assert set(_замер()) == set(whitelist.SAMPLE_FIELDS)
