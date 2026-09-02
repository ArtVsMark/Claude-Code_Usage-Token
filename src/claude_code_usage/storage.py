"""Хранилище замеров: приватный git-репозиторий, append-only (#2).

## Путь — из окружения, не из кода

Хранилище мейнтейнера постороннему человеку недоступно, и захардкоженный путь
превратил бы инструмент в личный скрипт. Поэтому путь задаётся переменной
`CLAUDE_CODE_USAGE_STORE` либо флагом `--store`.

**Файла конфигурации намеренно нет.** Третий источник истины пришлось бы
держать в согласии с двумя первыми, а надобности в нём пока не возникло:
переменная работает в обоих окнах одинаково. Появится случай, когда её не
хватает, — появится и конфиг, вместе с этим случаем.

## Готовность проверяется ДО первой записи

`docs/storage-setup.md` называет ловушку прямо: без `merge=union` замеры из
двух окон дают конфликт **через несколько замеров, а не при первом**. То есть
неготовое хранилище отказывает не там и не тогда, где ошиблись, и связать отказ
с причиной некому.

Поэтому проверка идёт до записи и называет всё несделанное разом, а не по
одному пункту за запуск.

## Порядок строк не гарантирован

`merge=union` объединяет, но не сортирует. Поэтому «когда был прошлый замер» —
это **максимум по `ts`**, а не последняя строка файла. На одном окне разницы
нет, и код, читающий последнюю строку, работал бы годами, пока не появится
второе окно.

## Замер не теряется

Порядок такой: дописать → закоммитить → запушить. Если пуш не прошёл, строка
уже в файле и в истории: сообщение говорит, что именно осталось локально, а не
делает вид, что всё уехало.

## Удачный пуш ещё не значит «наша строка в файле»

Замер на двух настоящих клонах: оба окна сняли замер в одну и ту же секунду,
строки вышли байт-в-байт одинаковыми, и `git pull --rebase` перед второй
попыткой **отбросил коммит второго окна** как уже применённый. Коммитов в
истории стало на один меньше, чем замеров.

Потери при этом не было, и выдавать её за потерю нельзя: строка в файле есть,
её записало первое окно той же секундой, а две одинаковые строки несут ровно
столько же, сколько одна. Отчёт «уехал» был верен по существу.

Но замер показал другое: **код возврата `git push` — не доказательство того,
что наши строки на месте.** Между «дописали» и «отправили» лежит
перебазирование, которое вправе выбросить коммит целиком. Поэтому после пуша
идёт :func:`confirm` — она сверяет с файлом СОДЕРЖИМОЕ строк, а не число
коммитов, и потому одинаковую строку от чужой не отличает и отличать не
должна.
"""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

#: Откуда берётся путь к хранилищу.
ENV_STORE = "CLAUDE_CODE_USAGE_STORE"

#: Файл замеров внутри хранилища.
SAMPLES = "samples/usage.jsonl"

#: Строка слияния, без которой два окна дают конфликт.
MERGE_RULE = f"{SAMPLES} merge=union"


class StoreError(RuntimeError):
    """Хранилище не задано, не готово или не приняло запись."""


@dataclass(frozen=True)
class PushResult:
    """Уехал ли замер. `False` — записан локально, но не отправлен."""

    pushed: bool
    detail: str


def store_path(explicit: str | None = None) -> Path:
    """Путь к хранилищу: флаг важнее переменной, иначе внятный отказ."""
    сырой = explicit or os.environ.get(ENV_STORE, "").strip()
    if not сырой:
        raise StoreError(
            f"путь к хранилищу не задан: укажите --store или переменную "
            f"{ENV_STORE}. Подготовка хранилища описана в docs/storage-setup.md"
        )
    return Path(сырой).expanduser()


def readiness(store: Path) -> list[str]:
    """Чего не хватает хранилищу. Пустой список — готово.

    Пункты называются все сразу: чинить по одному за запуск дороже, а забыть
    второй, починив первый, — обычное дело.
    """
    беды: list[str] = []
    if not store.is_dir():
        return [f"каталога {store} нет"]
    if not (store / ".git").exists():
        беды.append(f"{store} не git-репозиторий — замеры некуда версионировать")
    if not (store / SAMPLES).is_file():
        беды.append(
            f"нет файла {SAMPLES}: он должен существовать и быть закоммичен "
            "(пустой годится)"
        )
    атрибуты = store / ".gitattributes"
    правило_есть = атрибуты.is_file() and any(
        строка.split("#", 1)[0].strip() == MERGE_RULE
        for строка in атрибуты.read_text(encoding="utf-8").splitlines()
    )
    if not правило_есть:
        беды.append(
            f"в .gitattributes нет строки «{MERGE_RULE}» — замеры из двух окон "
            "дадут конфликт, и не при первом запуске, а через несколько"
        )
    return беды


def read_rows(store: Path) -> list[dict[str, Any]]:
    """Прочитать записанные замеры. Битая строка не роняет чтение, но считается.

    Файл переживает несколько версий инструмента и слияния из разных окон;
    единственная испорченная строка не повод отказаться читать остальные.
    """
    файл = store / SAMPLES
    if not файл.is_file():
        return []
    строки: list[dict[str, Any]] = []
    for строка in файл.read_text(encoding="utf-8").splitlines():
        строка = строка.strip()
        if not строка:
            continue
        try:
            разобрано = json.loads(строка)
        except json.JSONDecodeError:
            continue
        if isinstance(разобрано, dict):
            строки.append(разобрано)
    return строки


def last_ts(rows: Sequence[dict[str, Any]], *, source: str) -> str | None:
    """Когда был последний замер этого источника — **максимум**, не хвост файла.

    Слияние `union` объединяет строки, но не сортирует их. Взять последнюю
    строку значило бы получить верный ответ на одном окне и молча неверный на
    двух.
    """
    метки = [
        строка["ts"]
        for строка in rows
        if строка.get("source") == source and isinstance(строка.get("ts"), str)
    ]
    return max(метки) if метки else None


def append(store: Path, rows: Sequence[dict[str, Any]]) -> int:
    """Дописать замеры. Только дописывание: строки не правятся и не удаляются."""
    if not rows:
        return 0
    файл = store / SAMPLES
    файл.parent.mkdir(parents=True, exist_ok=True)
    with файл.open("a", encoding="utf-8") as поток:
        for строка in rows:
            поток.write(json.dumps(строка, ensure_ascii=False, sort_keys=True) + "\n")
    return len(rows)


def confirm(store: Path, rows: Sequence[dict[str, Any]]) -> list[dict[str, Any]]:
    """Каких из этих замеров в файле нет. Пустой список — все на месте.

    Нужна именно после пуша: перебазирование перед повторной попыткой молча
    отбрасывает коммит, диff которого уже применён, — и `git push` возвращает
    успех, ничего не отправив.

    Сравнение идёт по СОДЕРЖИМОМУ, а не по числу строк: одинаковую строку
    другое окно могло записать той же секундой, и тогда замер записан — просто
    не нами.
    """
    записанные = read_rows(store)
    ключи = {
        json.dumps(строка, ensure_ascii=False, sort_keys=True) for строка in записанные
    }
    return [
        строка
        for строка in rows
        if json.dumps(строка, ensure_ascii=False, sort_keys=True) not in ключи
    ]


def _git(store: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(store), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
    )


def commit(store: Path, message: str) -> bool:
    """Закоммитить файл замеров. `False` — коммитить было нечего."""
    добавление = _git(store, "add", "--", SAMPLES)
    if добавление.returncode:
        raise StoreError(f"git add не отработал: {добавление.stderr.strip()}")
    if not _git(store, "diff", "--cached", "--quiet", "--", SAMPLES).returncode:
        return False
    ответ = _git(store, "commit", "-m", message, "--", SAMPLES)
    if ответ.returncode:
        raise StoreError(f"git commit не отработал: {ответ.stderr.strip()}")
    return True


def push(store: Path, *, attempts: int = 3) -> PushResult:
    """Отправить замер. Отказ — не потеря: строка уже в файле и в истории.

    Перед каждой повторной попыткой ветка подтягивается с `--rebase`: два окна
    пишут в один файл, и разошедшиеся истории — обычное состояние, а не сбой.
    """
    последняя = ""
    for попытка in range(1, attempts + 1):
        ответ = _git(store, "push")
        if not ответ.returncode:
            return PushResult(True, f"уехал с попытки {попытка}")
        последняя = (ответ.stderr or ответ.stdout).strip()
        if попытка < attempts:
            _git(store, "pull", "--rebase")
    return PushResult(
        False,
        f"пуш не прошёл за {attempts} попытки: {последняя}. Замер записан и "
        f"закоммичен локально в {store / SAMPLES} — он не потерян, но и не "
        "уехал; отправьте вручную",
    )
