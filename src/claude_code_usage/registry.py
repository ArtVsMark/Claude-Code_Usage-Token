"""Разбор выгрузки реестра сессий (#2).

## Почему разбор, а не запрос

Реестр живёт в MCP, а MCP есть у **окна**, не у процесса Python. Инвариант
проекта из `CLAUDE.md` — «замер делает окно» — отсюда и следует: окно
спрашивает реестр само и кладёт ответ в файл, а инструмент этот файл разбирает.
Ходить в реестр отсюда нечем, и притворяться, что можно, было бы хуже, чем не
уметь.

## Форма выгрузки взята замером, а не из спецификации

`docs/spec.md` показывал `usage` и `rate_limit_info` на верхнем уровне записи.
Живой ответ (замер 2026-09-02) устроен иначе:

```
{"ccr": {"data": [{"id": "session_…",
                   "external_metadata": {"usage": {…},
                                         "rate_limit_info": {…}}}],
         "has_more": true}}
```

То есть оба блока лежат **под `external_metadata`**, запись обёрнута в `ccr`, а
список ещё и страничный. Читатель, написанный по документу, не нашёл бы ничего
и сказал бы «замерять нечего» — отказ верный по форме и ложный по сути.

## Чего здесь нет

Ни `title`, ни `task_summary`, ни ветки, ни адреса репозитория. Записи чужих
сессий приходят с текстом, который писали другие люди, и в замер он не попадает
по построению: наружу отдаются только идентификатор, `usage` и
`rate_limit_info`, а дальше их фильтрует белый список.

## Страницы

`has_more` в разборе не участвует: доклеивать страницы — дело того, кто
спрашивает реестр. Но молчать о нём нельзя, иначе «прошли по всем сессиям»
окажется неправдой на первой же сотне, поэтому :func:`records` отдаёт признак
вместе с записями.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

#: Где в живой записи лежат оба нужных блока.
NESTED_UNDER = "external_metadata"

#: Ключи блоков — как их называет площадка.
USAGE_KEY = "usage"
LIMIT_KEY = "rate_limit_info"


@dataclass(frozen=True)
class Record:
    """Запись реестра, приведённая к тому, что ждёт белый список."""

    session: str
    payload: dict[str, Any]


@dataclass(frozen=True)
class Reading:
    """Разобранная выгрузка и её охват.

    `truncated` — часть вывода, а не отладка: «замер по всем сессиям», снятый с
    одной страницы, занизит сумму на неизвестную долю, и по файлу это не
    отличить от «сессий было столько».
    """

    records: list[Record]
    truncated: bool
    skipped: int


def _unwrap(payload: Any) -> list[Any]:
    """Достать список записей из любой из четырёх форм выгрузки."""
    if isinstance(payload, dict) and "ccr" in payload:
        payload = payload["ccr"]
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return list(payload["data"])
    if isinstance(payload, list):
        return list(payload)
    if isinstance(payload, dict):
        return [payload]
    raise ValueError(
        "выгрузка реестра не разобрана: ожидался объект или список записей, "
        f"пришло {type(payload).__name__}"
    )


def _truncated(payload: Any) -> bool:
    if isinstance(payload, dict) and "ccr" in payload:
        payload = payload["ccr"]
    return bool(isinstance(payload, dict) and payload.get("has_more"))


def records(payload: Any) -> Reading:
    """Привести выгрузку к записям, которые понимает `whitelist.build_sample`.

    Запись без `usage` или `rate_limit_info` пропускается и **считается**: у
    мостовых окон блока расхода нет вовсе, это штатное состояние реестра, а не
    поломка. Молчаливый пропуск был бы хуже: «сессий пять» и «сессий пять, из
    них две без расхода» — разные утверждения о полноте суммы (#13).
    """
    сырые = _unwrap(payload)
    собранные: list[Record] = []
    пропущено = 0
    for запись in сырые:
        if not isinstance(запись, dict):
            пропущено += 1
            continue
        номер = запись.get("id") or запись.get("session_id")
        внутри = запись.get(NESTED_UNDER)
        источник = внутри if isinstance(внутри, dict) else запись
        usage = источник.get(USAGE_KEY)
        limit = источник.get(LIMIT_KEY)
        if (
            not isinstance(номер, str)
            or not isinstance(usage, dict)
            or not isinstance(limit, dict)
        ):
            пропущено += 1
            continue
        собранные.append(
            Record(session=номер, payload={USAGE_KEY: usage, LIMIT_KEY: limit})
        )
    return Reading(records=собранные, truncated=_truncated(payload), skipped=пропущено)
