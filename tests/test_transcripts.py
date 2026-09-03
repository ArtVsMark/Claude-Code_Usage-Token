"""Проверки чтения локальных транскриптов (#13).

Источник нужен потому, что реестр видит **не все окна**: расход мостовых и
локальных окон в нём не виден вовсе, а светофор реагирует на весь расход
аккаунта. Значит шкала калибруется по заниженной сумме, и занижена она на
непостоянную долю.

Подделки здесь повторяют форму НАСТОЯЩЕЙ записи — той, что лежит в транскрипте
живой сессии. Подделка, придуманная из головы, проверяла бы замысел, а не
источник: ровно на этом уже один механизм оказался сломанным две недели.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from claude_code_usage import transcripts, whitelist

#: Форма записи, снятая с живого транскрипта. Лишние поля оставлены нарочно:
#: они и есть то, что читатель обязан пропускать, не спотыкаясь.
ЖИВАЯ_ЗАПИСЬ: dict[str, Any] = {
    "parentUuid": "…",
    "isSidechain": False,
    "type": "assistant",
    "uuid": "…",
    "timestamp": "2026-09-02T09:00:00.000Z",
    "sessionId": "e06f1808-5efc-53c7-b8c8-5645ba1103c7",
    "cwd": "/home/user/claude-code-usage",
    "gitBranch": "agent/transcripts-13",
    # `requestId` и `message.id` — то, чего этой подделке НЕ ХВАТАЛО, и потому
    # двойной счёт по блокам содержимого не ловился ничем (#52). Настоящая
    # запись их несёт всегда; подделка без них проверяла замысел, а не форму.
    "requestId": "req_011Ceg5L3nyMeiot",
    "apiBlockIndex": 0,
    "message": {
        "id": "msg_011Ceg5L4ozV2uApHD37cB4x",
        "model": "claude-opus-5",
        "role": "assistant",
        "usage": {
            "input_tokens": 2,
            "cache_creation_input_tokens": 55513,
            "cache_read_input_tokens": 100,
            "output_tokens": 168,
            "output_tokens_details": {"thinking_tokens": 20},
            "server_tool_use": {"web_search_requests": 0},
            "service_tier": "standard",
            "cache_creation": {"ephemeral_5m_input_tokens": 55513},
            "inference_geo": "…",
            "iterations": [{"input_tokens": 2}],
            "speed": "fast",
        },
    },
}


def _файл(каталог: Path, имя: str, записи: list[object]) -> Path:
    каталог.mkdir(parents=True, exist_ok=True)
    путь = каталог / имя
    путь.write_text(
        "\n".join(
            строка
            if isinstance(строка, str)
            else json.dumps(строка, ensure_ascii=False)
            for строка in записи
        )
        + "\n",
        encoding="utf-8",
    )
    return путь


# ── чтение ────────────────────────────────────────────────────────────────


def _другой_ответ(**поля: object) -> dict[str, object]:
    """Копия живой записи, но это ДРУГОЙ ответ модели — со своими ключами.

    Раньше здесь хватало смены отметки времени. Теперь нет, и это не
    придирка к оформлению: две строки с одним `message.id` — один ответ,
    записанный по блокам содержимого, и складывать их дважды нельзя (#52).
    """
    запись = dict(ЖИВАЯ_ЗАПИСЬ, requestId="req_второй", **поля)
    запись["message"] = dict(ЖИВАЯ_ЗАПИСЬ["message"], id="msg_второй")
    return запись


def test_расход_складывается_по_сессиям(tmp_path: Path) -> None:
    вторая = _другой_ответ(timestamp="2026-09-02T10:00:00.000Z")
    _файл(tmp_path / "проект", "a.jsonl", [ЖИВАЯ_ЗАПИСЬ, вторая])

    итоги, охват = transcripts.scan(transcripts.transcript_files(tmp_path))

    итог = итоги[ЖИВАЯ_ЗАПИСЬ["sessionId"]]
    assert итог.messages == 2
    assert итог.numbers == {
        "input": 4,
        "output": 336,
        "cache_read": 200,
        "cache_write": 111026,
    }
    assert итог.first_ts < итог.last_ts
    assert охват.counted == 2


def test_лишние_поля_usage_не_считаются_незнакомыми() -> None:
    """`service_tier`, `speed`, `iterations` — не слагаемые, и это известно.

    Без списка известных-не-слагаемых сигнал «источник изменился» тонул бы в
    постоянном шуме: они есть в каждой записи.
    """
    итог = transcripts.Totals(session="s")

    незнакомые = итог.add(ЖИВАЯ_ЗАПИСЬ["message"]["usage"], "")

    assert незнакомые == set()


def test_новое_поле_usage_называется_а_не_глотается() -> None:
    """Граница здесь мягче, чем у реестра, — и это названо, а не подразумевается.

    В реестре незнакомое поле роняет замер: состав узкий, новое поле там скорее
    всего новое слагаемое. В транскрипте состав заведомо шире складываемого, и
    отказ означал бы не собрать ничего никогда. Поэтому считаем и называем.
    """
    итог = transcripts.Totals(session="s")

    незнакомые = итог.add({"input_tokens": 1, "quantum_tokens": 7}, "")

    assert незнакомые == {"quantum_tokens"}
    assert итог.numbers == {"input": 1}


def test_нечисловое_значение_не_складывается() -> None:
    """Реестр может вернуть мусор — транскрипт тоже.

    Строка вместо числа сложилась бы в `TypeError`, а `True` — в единицу:
    булево в Python целое, и `1 + True` даёт 2. Обе поломки тихие: первая
    роняет сбор целиком, вторая портит сумму на неизвестную величину.
    """
    итог = transcripts.Totals(session="s")

    итог.add(
        {
            "input_tokens": "много",
            "output_tokens": True,
            "cache_read_input_tokens": None,
        },
        "",
    )

    assert итог.numbers == {}, "в сумму попало нечисло"
    assert итог.messages == 1, "сообщение всё равно посчитано"


def test_битая_строка_считается_а_не_роняет(tmp_path: Path) -> None:
    """Транскрипт пишет живой процесс: последняя строка бывает обрезана."""
    _файл(tmp_path, "a.jsonl", [ЖИВАЯ_ЗАПИСЬ, '{"обрезано": ', "[]"])

    итоги, охват = transcripts.scan(transcripts.transcript_files(tmp_path))

    assert охват.unreadable == 2
    assert охват.counted == 1
    assert итоги


def test_записи_без_usage_пропускаются(tmp_path: Path) -> None:
    """Пользовательские сообщения, вложения, служебное — их большинство."""
    _файл(
        tmp_path,
        "a.jsonl",
        [
            {"type": "user", "sessionId": "s", "message": {"role": "user"}},
            {"type": "attachment", "sessionId": "s"},
            ЖИВАЯ_ЗАПИСЬ,
        ],
    )

    итоги, охват = transcripts.scan(transcripts.transcript_files(tmp_path))

    assert охват.lines == 3
    assert охват.counted == 1
    assert len(итоги) == 1


def test_каталога_нет_это_пусто_а_не_отказ(tmp_path: Path) -> None:
    """Локальных окон может не быть вовсе — это законное состояние."""
    assert transcripts.transcript_files(tmp_path / "нет") == []


def test_охват_попадает_в_вывод(tmp_path: Path) -> None:
    """Без чисел охвата слепота источника неотличима от чистого результата."""
    _файл(tmp_path, "a.jsonl", [ЖИВАЯ_ЗАПИСЬ, "мусор"])

    _, охват = transcripts.scan(transcripts.transcript_files(tmp_path))

    напечатано = str(охват)
    assert "транскриптов 1" in напечатано
    assert "нечитаемых 1" in напечатано


# ── строка замера из транскрипта ──────────────────────────────────────────


def test_замер_из_транскрипта_несёт_источник() -> None:
    замер = whitelist.build_transcript_sample(
        {"input": 1, "output": 2, "cache_read": 3, "cache_write": 4},
        ts="2026-09-02T09:00:00Z",
        session_id="s",
    )

    assert замер["source"] == "transcript"
    assert whitelist.audit(замер) == [], "в строке нет полей вне белого списка"


def test_у_транскриптной_строки_нет_стоимости() -> None:
    """`cost_usd` отдаёт только реестр.

    Считать его по ценам модели — выдать оценку за факт; писать нулём —
    соврать молча. Поэтому поля просто нет, и читатель это видит.
    """
    замер = whitelist.build_transcript_sample({"input": 1}, ts="t", session_id="s")

    assert "cost_usd" not in замер
    assert {"cost_usd"} == whitelist.REGISTRY_ONLY


def test_у_транскриптной_строки_нет_светофора() -> None:
    """Состояние лимита приходит с записью реестра, выдумывать его нечем."""
    замер = whitelist.build_transcript_sample({"input": 1}, ts="t", session_id="s")

    assert not {"type", "status", "resets_at"} & set(замер)


def test_незнакомое_число_в_замер_не_попадает() -> None:
    with pytest.raises(whitelist.UnknownUsageFieldError):
        whitelist.build_transcript_sample({"quantum": 1}, ts="t", session_id="s")


def test_замер_реестра_тоже_помечен_источником() -> None:
    """Иначе прежние строки были бы неотличимы от транскриптных."""
    запись = {
        "usage": {
            "cache_read_tokens": 1,
            "cache_write_tokens": 2,
            "input_tokens": 3,
            "output_tokens": 4,
            "cost_usd": 5.0,
        },
        "rate_limit_info": {
            "rateLimitType": "five_hour",
            "status": "allowed",
            "resetsAt": 1,
        },
    }

    замер = whitelist.build_sample(
        запись, ts="t", session_id="s", complete=True, sessions=1
    )

    assert замер["source"] == "registry"
    assert замер["source"] in whitelist.SOURCES


# ── один ответ — несколько строк (#52) ────────────────────────────────────
#
# Транскрипт пишет по строке на блок содержимого: текст, рассуждение, каждый
# вызов инструмента. `usage` в них стоит ОДИН И ТОТ ЖЕ — полный расход ответа,
# а не доля. Замер 2026-09-03: 267 строк на 132 ответа, расход завышался втрое.


def _блок(индекс: int) -> dict[str, object]:
    """Ещё одна строка ТОГО ЖЕ ответа: свой uuid, общий `message.id`."""
    return dict(ЖИВАЯ_ЗАПИСЬ, uuid=f"блок-{индекс}", apiBlockIndex=индекс)


def test_строки_одного_ответа_складываются_один_раз(tmp_path: Path) -> None:
    """Ровно инцидент: расход множился на число блоков содержимого."""
    _файл(tmp_path / "проект", "a.jsonl", [_блок(0), _блок(1), _блок(2)])

    итоги, охват = transcripts.scan(transcripts.transcript_files(tmp_path))

    итог = итоги[ЖИВАЯ_ЗАПИСЬ["sessionId"]]
    assert итог.messages == 1, "три строки — это один ответ"
    assert итог.numbers["output"] == 168, "расход ответа, а не утроенный"
    assert охват.counted == 1
    assert охват.duplicates == 2


def test_повторы_называются_в_охвате(tmp_path: Path) -> None:
    """Без числа отброшенных дедупликация неотличима от источника без повторов.

    Та же причина, по которой охват вообще печатается: «сложено 1» без «из
    3 строк» не даёт понять, что произошло — и молчаливая потеря расхода
    выглядела бы точно так же.
    """
    _файл(tmp_path / "проект", "a.jsonl", [_блок(0), _блок(1)])

    _, охват = transcripts.scan(transcripts.transcript_files(tmp_path))

    assert "повторов ответа 1" in str(охват)


def test_разные_ответы_складываются_оба(tmp_path: Path) -> None:
    """Дедупликация не должна съедать соседние ответы: ключ у них разный."""
    _файл(tmp_path / "проект", "a.jsonl", [ЖИВАЯ_ЗАПИСЬ, _другой_ответ()])

    итоги, охват = transcripts.scan(transcripts.transcript_files(tmp_path))

    assert итоги[ЖИВАЯ_ЗАПИСЬ["sessionId"]].numbers["output"] == 336
    assert охват.duplicates == 0


def test_ключ_запроса_годится_когда_ключа_ответа_нет(tmp_path: Path) -> None:
    """Два ключа, а не один: у старых записей `message.id` может не быть."""
    без_id = dict(_блок(1))
    без_id["message"] = {к: з for к, з in ЖИВАЯ_ЗАПИСЬ["message"].items() if к != "id"}
    _файл(tmp_path / "проект", "a.jsonl", [ЖИВАЯ_ЗАПИСЬ, без_id])

    _, охват = transcripts.scan(transcripts.transcript_files(tmp_path))

    assert охват.duplicates == 1, "requestId у обеих строк общий — это один ответ"


def test_запись_без_ключей_считается_а_не_теряется(tmp_path: Path) -> None:
    """Ошибка в сторону завышения — видимая; в сторону потери — молчаливая.

    Строка без обоих идентификаторов неотличима от самостоятельного ответа.
    Пропустить её значило бы потерять расход и никак этого не показать, а
    посчитать — завысить на величину, которая видна в охвате.
    """
    голая = {к: з for к, з in ЖИВАЯ_ЗАПИСЬ.items() if к != "requestId"}
    голая["message"] = {к: з for к, з in ЖИВАЯ_ЗАПИСЬ["message"].items() if к != "id"}
    _файл(tmp_path / "проект", "a.jsonl", [голая, dict(голая, uuid="вторая")])

    итоги, охват = transcripts.scan(transcripts.transcript_files(tmp_path))

    assert итоги[ЖИВАЯ_ЗАПИСЬ["sessionId"]].messages == 2
    assert охват.duplicates == 0
