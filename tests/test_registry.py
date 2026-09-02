"""Разбор выгрузки реестра — по замеру живого ответа, а не по спецификации (#2)."""

from __future__ import annotations

from typing import Any

import pytest

from claude_code_usage import registry

#: Форма живого ответа, снятая 2026-09-02 с реестра сессий. Именно она, а не
#: та, что была нарисована в `docs/spec.md`: там оба блока лежали на верхнем
#: уровне, и читатель по документу не нашёл бы ничего.
ЖИВАЯ_ЗАПИСЬ: dict[str, Any] = {
    "id": "session_01BxwpNgCN95B2xkhuGDPKUC",
    "title": "[WEB] claude-code-usage",
    "session_status": "SESSION_STATUS_RUNNING",
    "external_metadata": {
        "task_summary": "текст, который писал человек",
        "rate_limit_info": {
            "isUsingOverage": False,
            "rateLimitType": "five_hour",
            "resetsAt": 1788370800,
            "status": "allowed",
        },
        "usage": {
            "cache_read_tokens": 282296368,
            "cache_write_tokens": 3881133,
            "cost_usd": 266.03,
            "input_tokens": 17845,
            "output_tokens": 533563,
        },
    },
}


def test_блоки_берутся_из_external_metadata() -> None:
    """Ровно то, на чём сломался бы читатель, написанный по документу."""
    чтение = registry.records({"ccr": {"data": [ЖИВАЯ_ЗАПИСЬ]}})

    assert len(чтение.records) == 1
    запись = чтение.records[0]
    assert запись.session == "session_01BxwpNgCN95B2xkhuGDPKUC"
    assert запись.payload["usage"]["input_tokens"] == 17845
    assert запись.payload["rate_limit_info"]["status"] == "allowed"


def test_наружу_не_выходит_ничего_кроме_расхода_и_светофора() -> None:
    """Записи чужих сессий несут текст, который писали другие люди.

    Он не попадает в замер по построению, а не потому что белый список поймает
    его этажом выше: два заслона на одном пути дешевле одного.
    """
    запись = registry.records({"ccr": {"data": [ЖИВАЯ_ЗАПИСЬ]}}).records[0]

    assert set(запись.payload) == {"usage", "rate_limit_info"}
    плоско = str(запись.payload)
    assert "claude-code-usage" not in плоско
    assert "текст, который писал человек" not in плоско


def test_плоская_форма_из_спецификации_тоже_принимается() -> None:
    """Документ мог быть верен для другой версии площадки — ломать не за что."""
    плоская = {
        "id": "s1",
        "usage": {"input_tokens": 1},
        "rate_limit_info": {"status": "allowed"},
    }

    assert registry.records([плоская]).records[0].session == "s1"


@pytest.mark.parametrize(
    "выгрузка",
    [
        {"ccr": {"data": [ЖИВАЯ_ЗАПИСЬ]}},
        {"data": [ЖИВАЯ_ЗАПИСЬ]},
        [ЖИВАЯ_ЗАПИСЬ],
        ЖИВАЯ_ЗАПИСЬ,
    ],
    ids=["ccr.data", "data", "список", "одна запись"],
)
def test_четыре_формы_выгрузки(выгрузка: Any) -> None:
    """`list_sessions` и `get_session` заворачивают ответ по-разному.

    Окно выгружает то, что ему отдали, и требовать от него разворачивать
    обёртку значило бы перенести знание о форме площадки в инструкцию человеку.
    """
    assert len(registry.records(выгрузка).records) == 1


def test_неполная_выгрузка_называется_вслух() -> None:
    """«Прошли по всем сессиям» с одной страницы — неправда.

    Сумма занижена на неизвестную долю, и по файлу замеров это не отличить от
    «сессий было столько».
    """
    чтение = registry.records({"ccr": {"data": [ЖИВАЯ_ЗАПИСЬ], "has_more": True}})

    assert чтение.truncated is True


def test_полная_выгрузка_не_кричит() -> None:
    assert registry.records({"ccr": {"data": [ЖИВАЯ_ЗАПИСЬ]}}).truncated is False


def test_сессия_без_расхода_пропускается_и_считается() -> None:
    """У мостовых окон блока расхода нет — это штатное состояние реестра.

    Но пропуск обязан быть посчитан: «сессий пять» и «сессий пять, из них две
    без расхода» — разные утверждения о полноте суммы (#13).
    """
    мост = {"id": "мост", "external_metadata": {"container_cc_version": "2.1.251"}}

    чтение = registry.records({"ccr": {"data": [ЖИВАЯ_ЗАПИСЬ, мост]}})

    assert len(чтение.records) == 1
    assert чтение.skipped == 1


def test_мусор_вместо_выгрузки_отказ_а_не_пустота() -> None:
    """Пустой ответ неотличим от «сессий нет», и это была бы тихая ложь."""
    with pytest.raises(ValueError, match="не разобрана"):
        registry.records("строка вместо выгрузки")
