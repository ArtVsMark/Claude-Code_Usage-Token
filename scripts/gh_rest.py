"""Единственный транспорт до GitHub: REST и только REST (#8).

## Почему не GraphQL

`CLAUDE.md` запрещает GraphQL, и запрет не про вкус. Одна GraphQL-операция
стоит ~300 points из 5000 в час, REST-запрос — 1 из 5000. Разница в триста раз,
и она уже дважды выжигала квоту в соседнем проекте: посреди работы команды
просто переставали отвечать.

Цена запрета названа честно: **авто-мержа GitHub у нас не будет.** Он
включается мутацией ``enablePullRequestAutoMerge``, REST-эквивалента нет, и
обойти это нечем. Поэтому очередь мержит сама — `scripts/merge_queue.py`, — а
решение «зелено ли» принимает `scripts/pr_ready.py` по трём правилам чтения
проверок из `CLAUDE.md`.

## Почему один модуль на весь конвейер

Токен, разбор ошибок, узнавание исчерпанной квоты и постраничная выдача должны
быть одинаковыми везде. Второй транспорт рядом разошёлся бы с первым молча — и
разошёлся бы именно в обработке отказов, то есть там, где это дороже всего.

## Чего этот модуль не делает

Не решает, что означает ответ. Он отдаёт разобранный JSON и внятно падает;
смысл ответов — дело вызывающего.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

#: Адрес площадки. В прогоне задан переменной, локально — умолчание.
API_URL = os.environ.get("GITHUB_API_URL", "https://api.github.com")

#: Сколько записей просить за раз. Сотня — потолок площадки; просить меньше
#: значит платить лишними запросами из той же квоты.
PER_PAGE = 100

#: Потолок страниц на один обход. Не оптимизация, а предохранитель: ошибка в
#: условии выхода превращает постраничную выдачу в бесконечный цикл, который
#: съедает квоту молча и до конца.
MAX_PAGES = 20


class GitHubError(RuntimeError):
    """Площадка ответила отказом. Текст называет код и тело ответа."""

    def __init__(self, method: str, path: str, status: int, body: str) -> None:
        super().__init__(f"{method} {path} → HTTP {status}: {body[:400]}")
        self.status = status
        self.body = body


def token() -> str:
    """Токен из окружения. Пусто — не исключение, а законное состояние.

    Прогон без секрета обязан **предупредить и выйти**, а не покраснеть:
    красное здесь означало бы поломку механизма, а не ненастроенное удобство.
    Решение принимает вызывающий, поэтому здесь просто пустая строка.
    """
    return os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or ""


def repository() -> str:
    """``владелец/репозиторий`` из окружения прогона."""
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    if not repo:
        raise GitHubError("GET", "-", 0, "GITHUB_REPOSITORY не задан")
    return repo


def request(
    method: str,
    path: str,
    *,
    body: dict[str, Any] | None = None,
    params: dict[str, str | int] | None = None,
) -> Any:
    """Один запрос к REST. Возвращает разобранный JSON либо ``None`` на 204."""
    url = f"{API_URL}{path}"
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"

    данные = None if body is None else json.dumps(body).encode("utf-8")
    заголовки = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "claude-code-usage-pipeline",
    }
    ключ = token()
    if ключ:
        заголовки["Authorization"] = f"Bearer {ключ}"
    if данные is not None:
        заголовки["Content-Type"] = "application/json"

    запрос = urllib.request.Request(url, data=данные, headers=заголовки, method=method)
    try:
        with urllib.request.urlopen(запрос) as ответ:
            сырое = ответ.read().decode("utf-8")
            return json.loads(сырое) if сырое else None
    except urllib.error.HTTPError as exc:
        raise GitHubError(method, path, exc.code, exc.read().decode("utf-8")) from exc
    except urllib.error.URLError as exc:
        raise GitHubError(method, path, 0, str(exc.reason)) from exc


def paged(path: str, *, params: dict[str, str | int] | None = None) -> list[Any]:
    """Собрать все страницы списка.

    Обход останавливается на неполной странице — признак последней. Потолок
    страниц не даёт зациклиться, если площадка вдруг начнёт отдавать полные
    страницы бесконечно.
    """
    собрано: list[Any] = []
    for страница in range(1, MAX_PAGES + 1):
        порция = request(
            "GET",
            path,
            params={**(params or {}), "per_page": PER_PAGE, "page": страница},
        )
        if not isinstance(порция, list):
            break
        собрано.extend(порция)
        if len(порция) < PER_PAGE:
            break
    return собрано
