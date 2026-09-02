"""Расход из локальных транскриптов сессий (#13).

Реестр сессий видит **не все окна**. Замер на живом аккаунте:

| окно | запись в реестре | `usage` в ней |
|---|:--:|:--:|
| облачное | есть | есть |
| мостовое (`bridge`, Remote Control) | есть | **нет** |
| локальное CLI без моста | **нет** | — |

Светофор при этом реагирует на **весь** расход аккаунта: лимит общий. Значит
переключение произойдёт при сумме меньше истинной, и шкала окажется занижена на
невидимую долю — причём непостоянную, потому что она зависит от того, сколько
человек работал локально. Это шум, а не смещение, и накоплением замеров он не
лечится.

Второй источник — транскрипты, которые каждая сессия пишет на своей машине.

## Чем этот источник отличается от реестра

**Он помессажный.** `usage` стоит на каждом ответе, а не агрегатом за окно: для
калибровки это лучше, потому что переключение светофора привязывается к
моменту.

**В нём нет стоимости.** `cost_usd` отдаёт только реестр. Из транскрипта
выводятся четыре числа расхода из пяти, и пятое взять неоткуда — считать его по
ценам модели значило бы выдать оценку за факт.

**Он живёт только на своей машине.** Транскрипт облачного окна исчезает вместе
с контейнером, поэтому источники не заменяют друг друга, а покрывают разное:
реестр — единственный по облачным окнам и переживает их; транскрипты —
единственные по локальным и мостовым, но только там, где они запускались.

## Граница «незнакомого» здесь другая, чем у реестра

В `whitelist` незнакомое поле **внутри** `usage` роняет замер: у реестра состав
узкий, и новое поле там — скорее всего новое слагаемое расхода. В транскрипте
состав заведомо шире складываемого: `service_tier`, `speed`, `iterations`,
`inference_geo` — не слагаемые, и отказывать на них значило бы не собрать ничего
никогда.

Поэтому здесь незнакомое **считается и называется**, а не роняет и не
пропускается молча: число попадает в охват, и читатель видит, что источник
изменился.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from pathlib import Path

#: Где Claude Code держит транскрипты. Путь берётся отсюда, но переопределяется
#: аргументом: посторонний человек должен направить инструмент в своё место,
#: ничего не правя.
DEFAULT_ROOT = Path.home() / ".claude" / "projects"

#: Складываемые поля `usage` и их имена в замере. Остальное — не слагаемые.
ADDENDS: dict[str, str] = {
    "input_tokens": "input",
    "output_tokens": "output",
    "cache_read_input_tokens": "cache_read",
    "cache_creation_input_tokens": "cache_write",
}

#: Поля `usage`, про которые известно, что они НЕ слагаемые. Список нужен не
#: для фильтрации, а для тишины: без него каждое из них попадало бы в счёт
#: незнакомого, и сигнал «источник изменился» утонул бы в постоянном шуме.
KNOWN_NOT_ADDENDS = frozenset(
    {
        "cache_creation",
        "inference_geo",
        "iterations",
        "output_tokens_details",
        "server_tool_use",
        "service_tier",
        "speed",
    }
)


@dataclass
class Totals:
    """Расход одной сессии, сложенный по её транскрипту."""

    session: str
    messages: int = 0
    first_ts: str = ""
    last_ts: str = ""
    numbers: dict[str, int] = field(default_factory=dict)

    def add(self, usage: dict[str, object], ts: str) -> set[str]:
        """Сложить один ответ. Возвращает незнакомые поля этого ответа."""
        self.messages += 1
        if ts:
            self.first_ts = min(self.first_ts, ts) if self.first_ts else ts
            self.last_ts = max(self.last_ts, ts)
        for ключ, имя in ADDENDS.items():
            значение = usage.get(ключ)
            if isinstance(значение, int) and not isinstance(значение, bool):
                self.numbers[имя] = self.numbers.get(имя, 0) + значение
        return set(usage) - set(ADDENDS) - KNOWN_NOT_ADDENDS


@dataclass
class Coverage:
    """Из чего сложен итог. Печатается вместе с ним, а не в отладку.

    Без этих чисел слепота источника неотличима от чистого результата: файл, из
    которого не разобралась ни одна строка, и файл без расхода дают один и тот
    же пустой ответ.
    """

    files: int = 0
    lines: int = 0
    counted: int = 0
    unreadable: int = 0
    unknown_fields: set[str] = field(default_factory=set)

    def __str__(self) -> str:
        хвост = (
            f", незнакомых полей {len(self.unknown_fields)}: "
            f"{', '.join(sorted(self.unknown_fields))}"
            if self.unknown_fields
            else ""
        )
        return (
            f"транскриптов {self.files}, строк {self.lines}, "
            f"с расходом {self.counted}, нечитаемых {self.unreadable}{хвост}"
        )


def transcript_files(root: Path) -> list[Path]:
    """Файлы транскриптов. Отсутствие каталога — не ошибка, а пустой ответ.

    Каталога нет там, где Claude Code ни разу не запускался локально: это
    законное состояние, и падать на нём значило бы требовать локальных окон от
    того, у кого их нет.

    Проверка на каталог названа честно: `rglob` на несуществующем пути и так
    отдаёт пустоту, так что мутация «убрать проверку» тестом не ловится. Она
    оставлена ради читателя — назвать намерение, — а не потому, что чинит
    наблюдавшееся падение.
    """
    return sorted(root.rglob("*.jsonl")) if root.is_dir() else []


def _records(path: Path) -> Iterator[tuple[dict[str, object] | None, bool]]:
    """Записи файла. Второе значение — «строка не разобралась».

    Битая строка пропускается, но СЧИТАЕТСЯ: транскрипт пишется живым
    процессом, и последняя строка бывает обрезана на полуслове.
    """
    with path.open(encoding="utf-8", errors="replace") as fh:
        for строка in fh:
            строка = строка.strip()
            if not строка:
                continue
            try:
                запись = json.loads(строка)
            except (ValueError, UnicodeDecodeError):
                yield None, True
                continue
            yield (
                (запись if isinstance(запись, dict) else None),
                not isinstance(запись, dict),
            )


def scan(paths: Iterable[Path]) -> tuple[dict[str, Totals], Coverage]:
    """Сложить расход по сессиям из перечисленных транскриптов."""
    итоги: dict[str, Totals] = {}
    охват = Coverage()

    for путь in paths:
        охват.files += 1
        for запись, битая in _records(путь):
            охват.lines += 1
            if битая or запись is None:
                охват.unreadable += 1
                continue
            session = запись.get("sessionId")
            message = запись.get("message")
            if not isinstance(session, str) or not isinstance(message, dict):
                continue
            usage = message.get("usage")
            if not isinstance(usage, dict):
                continue
            ts = запись.get("timestamp")
            итог = итоги.setdefault(session, Totals(session=session))
            охват.unknown_fields |= итог.add(usage, ts if isinstance(ts, str) else "")
            охват.counted += 1

    return итоги, охват
