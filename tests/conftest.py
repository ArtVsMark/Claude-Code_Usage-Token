"""Общие подделки для тестов.

Здесь живёт то, что нужно **больше чем одному** файлу тестов. Причина простая
и уже проверенная: вторая копия подделочной записи расходится с первой при
первой же правке формата — а расходится молча, потому что оба теста при этом
зелёные.

Живой случай: `ЖИВАЯ_ЗАПИСЬ` понадобилась и разбору транскриптов, и проверке
вывода команды. Копия в двух файлах стоила бы ровно того, о чём правило 022:
две реализации одного, и первая же правка их разведёт.
"""

from __future__ import annotations

from typing import Any

#: Форма записи, снятая с ЖИВОГО транскрипта. Лишние поля оставлены нарочно:
#: они и есть то, что читатель обязан пропускать, не спотыкаясь.
#:
#: `requestId` и `message.id` — то, чего подделке не хватало до #52, и потому
#: двойной счёт по блокам содержимого не ловился ничем. Настоящая запись их
#: несёт всегда; подделка без них проверяла замысел, а не форму.
ЖИВАЯ_ЗАПИСЬ: dict[str, Any] = {
    "parentUuid": "…",
    "isSidechain": False,
    "type": "assistant",
    "uuid": "…",
    "timestamp": "2026-09-02T09:00:00.000Z",
    "sessionId": "e06f1808-5efc-53c7-b8c8-5645ba1103c7",
    "cwd": "/home/user/claude-code-usage",
    "gitBranch": "agent/transcripts-13",
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
