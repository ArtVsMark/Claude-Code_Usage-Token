"""Единственный транспорт до GitHub: REST, GraphQL — закрытым списком (#8, #165).

## Почему REST, а GraphQL — закрытым списком

Одна GraphQL-операция стоит ~300 points из 5000 в час, REST-запрос — 1 из 5000.
Разница в триста раз, и она уже дважды выжигала квоту в соседнем проекте:
посреди работы команды просто переставали отвечать.

Поэтому GraphQL допустим только там, где у REST операции физически нет, и
каждая такая операция названа поимённо — `ГРАФQL_ОПЕРАЦИИ` ниже, единственный
вход `graphql()`. Сейчас это авто-мерж площадки: он включается мутацией
``enablePullRequestAutoMerge``, REST-эквивалента нет (#165). Решение «зелено
ли» по-прежнему принимает `scripts/pr_ready.py` по трём правилам чтения
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
import re
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

#: Срок одного запроса, в секундах. Без него `urlopen` ждёт ответа сколько
#: угодно: зависший обмен держит прогон до `timeout-minutes` джоба, а локально —
#: вечно. Гейт дедлайнов видит только `subprocess`, и сетевой вызов прошёл мимо
#: (#151, находка аудита #139).
#:
#: Полминуты — не с потолка: это срок транспорта проекта механизмов
#: (`packages/transport/ghrest.py`, `TIMEOUT = 30`), на который мы переезжаем
#: (#158). REST площадки отвечает за доли секунды, а медленнее всего — ответ
#: на сравнение веток, и у него запас в десятки раз.
REQUEST_TIMEOUT = 30


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
        with urllib.request.urlopen(запрос, timeout=REQUEST_TIMEOUT) as ответ:
            сырое = ответ.read().decode("utf-8")
            return json.loads(сырое) if сырое else None
    except urllib.error.HTTPError as exc:
        raise GitHubError(method, path, exc.code, exc.read().decode("utf-8")) from exc
    except urllib.error.URLError as exc:
        raise GitHubError(method, path, 0, str(exc.reason)) from exc
    except TimeoutError as exc:
        # Срок на соединении приходит `URLError`, а на ЧТЕНИИ — голым
        # `TimeoutError`, мимо ветки выше. Без этой ветки срок превратил бы
        # «площадка молчит» в трассировку вместо названного отказа.
        raise GitHubError(
            method, path, 0, f"нет ответа за {REQUEST_TIMEOUT} с ({exc})"
        ) from exc


#: GraphQL-операции, которые здесь разрешены. Закрытый список, и каждая — с
#: причиной: GraphQL стоит ~300 points против 1 у REST и уже выжигал квоту у
#: соседа, поэтому он допустим только там, где у REST операции физически нет
#: (правило 001). Авто-мерж — именно такой случай: решение владельца 2 октября
#: (#165), так же у конвейера механизмов (`ghrest.IDEMPOTENT`).
ГРАФQL_ОПЕРАЦИИ = frozenset(
    {"enablePullRequestAutoMerge", "disablePullRequestAutoMerge"}
)

_ОПЕРАЦИЯ_RE = re.compile(r"^\s*(?:mutation|query)\b[^{]*\{\s*(?P<имя>\w+)", re.DOTALL)


def graphql(запрос: str, переменные: dict[str, Any] | None = None) -> dict[str, Any]:
    """Одна GraphQL-операция из закрытого списка — и только она.

    Операция определяется первым полем запроса. Вне списка — отказ ДО обращения
    к площадке: квота не тратится на то, что всё равно запрещено. Ошибки
    GraphQL приходят с кодом 200 в поле `errors`, поэтому разбираются здесь, а
    не по статусу.
    """
    совпало = _ОПЕРАЦИЯ_RE.match(запрос)
    имя = совпало.group("имя") if совпало else "?"
    if имя not in ГРАФQL_ОПЕРАЦИИ:
        raise ValueError(
            f"GraphQL-операция «{имя}» вне закрытого списка "
            f"{sorted(ГРАФQL_ОПЕРАЦИИ)}: здесь всё по REST (правило 001, #165)"
        )
    ответ = request(
        "POST", "/graphql", body={"query": запрос, "variables": переменные or {}}
    )
    if not isinstance(ответ, dict):
        raise GitHubError("POST", "/graphql", 0, f"ответ не объект: {ответ!r}")
    if ответ.get("errors"):
        raise GitHubError(
            "POST", "/graphql", 200, json.dumps(ответ["errors"], ensure_ascii=False)
        )
    данные = ответ.get("data")
    return данные if isinstance(данные, dict) else {}


class TruncatedError(GitHubError):
    """Список прочитан не целиком. Это отказ, а не короткий ответ.

    Подкласс отказа площадки намеренно: вызывающие уже умеют с ним обходиться
    как с «не отработало», и прочитанный наполовину список не становится у них
    правдоподобным целым (правило 212 каталога).
    """

    def __init__(self, path: str, причина: str) -> None:
        super().__init__("GET", path, 0, причина)


def paged(
    path: str,
    *,
    params: dict[str, str | int] | None = None,
    key: str | None = None,
) -> list[Any]:
    """Собрать все страницы списка — или отказать, если целиком не вышло.

    Обход останавливается на неполной странице — признак последней. Потолок
    страниц не даёт зациклиться, если площадка вдруг начнёт отдавать полные
    страницы бесконечно; упёршийся в него обход — :class:`TruncatedError`, а не
    молча усечённый список.

    ``key`` — для списков в обёртке (``{"total_count": N, key: [...]}``):
    прогоны, проверки коммита. Собранное сверяется с ``total_count`` первой
    страницы. Граница названа: запись, появившаяся во время обхода, сдвигает
    страницы, и сверка числа такой сдвиг не видит — она ловит недобор, а не
    перестановку.
    """
    собрано: list[Any] = []
    всего: int | None = None
    for страница in range(1, MAX_PAGES + 1):
        ответ = request(
            "GET",
            path,
            params={**(params or {}), "per_page": PER_PAGE, "page": страница},
        )
        if key is not None:
            if not isinstance(ответ, dict):
                break
            if всего is None and isinstance(ответ.get("total_count"), int):
                всего = ответ["total_count"]
            ответ = ответ.get(key)
        if not isinstance(ответ, list):
            break
        собрано.extend(ответ)
        if len(ответ) < PER_PAGE:
            break
    else:
        raise TruncatedError(
            path,
            f"страниц больше {MAX_PAGES} по {PER_PAGE}: список прочитан не целиком",
        )
    if всего is not None and len(собрано) < всего:
        raise TruncatedError(
            path, f"площадка назвала {всего} записей, прочитано {len(собрано)}"
        )
    return собрано
