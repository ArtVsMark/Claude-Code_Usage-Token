"""Точка входа команды ``claude-code-usage-meter``.

Реализована одна команда — ``sample`` (#2). Остальные заведены в
``docs/spec.md``, но поведения не имеют, и точка входа **отказывает громко**:
ненулевой код возврата и сообщение, называющее, что именно не вышло. Молча
вернуть ноль значило бы соврать дважды — человеку и гейту.

## Почему `sample` берёт реестр из файла

Реестр живёт в MCP, а MCP есть у окна, не у процесса Python: инвариант «замер
делает окно» из `CLAUDE.md`. Окно спрашивает реестр само и кладёт ответ в файл;
``sample --registry`` его разбирает. Разбор формы — в ``registry.py``.

Транскрипты, наоборот, лежат на диске, и их инструмент читает сам.

## Ни одна строка не пишется, пока не проверены все

Отказ на середине оставил бы половину замера в append-only файле, а удалить её
нельзя — только дописать опровержение. Поэтому сборка идёт целиком, потом
белый список, и лишь потом запись.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import registry, storage, transcripts, whitelist
from .output import force_utf8_output

#: Команды из ``docs/spec.md``, § «Что инструмент делает».
COMMANDS: tuple[str, ...] = ("sample", "report", "calibrate")

#: Ошибка вызова: команды нет, аргументы не те, хранилище не задано.
EXIT_USAGE = 2

#: Работа не вышла: реестр отдал незнакомое, замер не записался.
EXIT_FAILED = 1

#: Сколько ждать между замерами по умолчанию.
#:
#: Пятнадцать минут — не круглое число из воздуха, а частота, при которой
#: пятичасовое окно набирает два десятка точек, а семидневное — под семьсот.
#: Чаще смысла нет: светофор переключается за часы, и лишние строки дают шум и
#: пачки коммитов (#2, `docs/spec.md` § «Частота»).
DEFAULT_INTERVAL_MINUTES = 15

_ISSUE_BY_COMMAND = {"report": 1, "calibrate": 1}


def _known() -> str:
    return ", ".join(COMMANDS)


def now_stamp(clock: dt.datetime | None = None) -> str:
    """Метка времени замера: UTC, до секунды, как в `docs/spec.md`."""
    момент = clock or dt.datetime.now(dt.UTC)
    return момент.astimezone(dt.UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _minutes_between(early: str, late: str) -> float | None:
    """Сколько минут между метками. `None` — метка нечитаемая."""
    try:
        было = dt.datetime.strptime(early, "%Y-%m-%dT%H:%M:%SZ")
        стало = dt.datetime.strptime(late, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError:
        return None
    return (стало - было).total_seconds() / 60


def rows_from_registry(payload: Any, *, ts: str) -> tuple[list[dict[str, Any]], str]:
    """Замеры из выгрузки реестра плюс строка про охват."""
    чтение = registry.records(payload)
    # Сколько записей БЫЛО в выгрузке, а не сколько дало расход: мостовые окна
    # без блока usage тоже существуют, и разница между этим числом и числом
    # строк в файле — их количество.
    всего = len(чтение.records) + чтение.skipped
    строки = [
        whitelist.build_sample(
            запись.payload,
            ts=ts,
            session_id=запись.session,
            complete=not чтение.truncated,
            sessions=всего,
        )
        for запись in чтение.records
    ]
    охват = f"реестр: сессий с расходом {len(строки)}, без расхода {чтение.skipped}"
    if чтение.truncated:
        охват += (
            "; ВЫГРУЗКА НЕПОЛНАЯ — реестр сообщил, что записи кончились не все, "
            "и сумма по ней занижена на неизвестную долю"
        )
    return строки, охват


def rows_from_transcripts(root: Path, *, ts: str) -> tuple[list[dict[str, Any]], str]:
    """Замеры из локальных транскриптов плюс строка про охват."""
    суммы, охват = transcripts.scan(transcripts.transcript_files(root))
    строки = [
        whitelist.build_transcript_sample(итог.numbers, ts=ts, session_id=сессия)
        for сессия, итог in sorted(суммы.items())
    ]
    описание = (
        f"транскрипты: сессий {len(строки)}, файлов {охват.files}, "
        f"строк с расходом {охват.counted}, нечитаемых {охват.unreadable}"
    )
    if охват.unknown_fields:
        описание += f", незнакомых полей {len(охват.unknown_fields)}"
    return строки, описание


def _sample(аргументы: argparse.Namespace) -> int:
    if not аргументы.registry and not аргументы.transcripts:
        print(
            "нечего замерять: укажите --registry ФАЙЛ и/или --transcripts. "
            "Реестр отдаёт MCP, то есть окно: выгрузите ответ в файл — "
            "инструмент до MCP не дотягивается по построению",
            file=sys.stderr,
        )
        return EXIT_USAGE

    try:
        хранилище = storage.store_path(аргументы.store)
    except storage.StoreError as отказ:
        print(str(отказ), file=sys.stderr)
        return EXIT_USAGE

    if not аргументы.dry_run:
        беды = storage.readiness(хранилище)
        if беды:
            print("хранилище не готово:", file=sys.stderr)
            for беда in беды:
                print(f"  — {беда}", file=sys.stderr)
            # Ссылкой, а не путём: у того, кто поставил пакет, каталога
            # docs/ нет вовсе — автор видит репозиторий, пользователь пакет.
            print(
                "  подготовка: https://github.com/ArtVsMark/Claude-Code_Usage-Token/blob/main/docs/storage-setup.md",
                file=sys.stderr,
            )
            return EXIT_USAGE

    метка = now_stamp()
    строки: list[dict[str, Any]] = []
    охваты: list[str] = []

    try:
        if аргументы.registry:
            выгрузка = json.loads(Path(аргументы.registry).read_text(encoding="utf-8"))
            свежие, охват = rows_from_registry(выгрузка, ts=метка)
            строки += свежие
            охваты.append(охват)
        if аргументы.transcripts:
            корень = Path(аргументы.transcripts_root or transcripts.DEFAULT_ROOT)
            свежие, охват = rows_from_transcripts(корень, ts=метка)
            строки += свежие
            охваты.append(охват)
    except (OSError, json.JSONDecodeError) as отказ:
        print(f"выгрузку не прочитать: {отказ}", file=sys.stderr)
        return EXIT_FAILED
    except whitelist.UnknownUsageFieldError as отказ:
        print(str(отказ), file=sys.stderr)
        return EXIT_FAILED
    except ValueError as отказ:
        print(f"замер не собран: {отказ}", file=sys.stderr)
        return EXIT_FAILED

    for охват in охваты:
        print(охват)

    if not строки:
        print(
            "замерять нечего: ни одной сессии с расходом. Пустая строка не "
            "пишется — она неотличима от нулевого расхода",
            file=sys.stderr,
        )
        return EXIT_FAILED

    лишнее = {поле for строка in строки for поле in whitelist.audit(строка)}
    if лишнее:
        print(
            f"замер не записан: в строке поля вне белого списка — "
            f"{', '.join(sorted(лишнее))}. Хранилище приватное, но утечка в "
            "приватный репозиторий всё равно утечка",
            file=sys.stderr,
        )
        return EXIT_FAILED

    if аргументы.dry_run:
        for строка in строки:
            print(json.dumps(строка, ensure_ascii=False, sort_keys=True))
        print(f"строк собрано {len(строки)}, ничего не записано (--dry-run)")
        return 0

    рано = _too_soon(хранилище, строки, аргументы.min_interval, метка)
    if рано:
        print(рано)
        return 0

    записано = storage.append(хранилище, строки)
    print(f"записано строк {записано} в {хранилище / storage.SAMPLES}")

    if аргументы.no_push:
        print("пуш не запрашивался (--no-push): замер лежит локально")
        return 0

    try:
        новое = storage.commit(хранилище, f"замер {метка}: строк {записано}")
    except storage.StoreError as отказ:
        print(str(отказ), file=sys.stderr)
        return EXIT_FAILED
    if not новое:
        print("коммитить было нечего")
        return 0

    итог = storage.push(хранилище)
    if not итог.pushed:
        print(f"пуш: {итог.detail}", file=sys.stderr)
        return EXIT_FAILED

    # Удачный пуш ещё не значит «строка уехала»: перебазирование перед
    # повторной попыткой молча отбрасывает коммит с уже применённым диффом.
    пропавшие = storage.confirm(хранилище, строки)
    if пропавшие:
        print(
            f"пуш вернул успех, но {len(пропавшие)} строк в файле нет — "
            "перебазирование отбросило коммит. Замер НЕ записан; повторите "
            "с --min-interval 0",
            file=sys.stderr,
        )
        return EXIT_FAILED
    print(f"пуш: {итог.detail}")
    return 0


def _too_soon(
    хранилище: Path,
    строки: Sequence[dict[str, Any]],
    интервал: float,
    метка: str,
) -> str | None:
    """Не рано ли писать. Порог — про частоту, а не про правильность замера."""
    if интервал <= 0:
        return None
    записанные = storage.read_rows(хранилище)
    for источник in sorted({строка["source"] for строка in строки}):
        прошлый = storage.last_ts(записанные, source=источник)
        if прошлый is None:
            return None
        прошло = _minutes_between(прошлый, метка)
        if прошло is None or прошло >= интервал:
            return None
    return (
        f"рано: прошлый замер был меньше {интервал:g} минут назад. Чаще писать "
        "незачем — светофор переключается за часы, а лишние строки дают шум "
        "и пачки коммитов. Порог снимается флагом --min-interval 0"
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="claude-code-usage-meter")
    подкоманды = parser.add_subparsers(dest="command")

    замер = подкоманды.add_parser("sample", help="снять замер и дописать в хранилище")
    замер.add_argument(
        "--registry",
        metavar="ФАЙЛ",
        help="выгрузка реестра сессий в JSON — её делает окно, у которого есть MCP",
    )
    замер.add_argument(
        "--transcripts",
        action="store_true",
        help="сложить расход из локальных транскриптов",
    )
    замер.add_argument(
        "--transcripts-root",
        metavar="КАТАЛОГ",
        help=f"где лежат транскрипты (по умолчанию {transcripts.DEFAULT_ROOT})",
    )
    замер.add_argument("--store", metavar="ПУТЬ", help="каталог хранилища замеров")
    замер.add_argument(
        "--min-interval",
        type=float,
        default=DEFAULT_INTERVAL_MINUTES,
        metavar="МИНУТ",
        help=(
            f"не писать чаще (по умолчанию {DEFAULT_INTERVAL_MINUTES}; 0 — снять порог)"
        ),
    )
    замер.add_argument(
        "--no-push",
        action="store_true",
        help="записать и закоммитить, но не отправлять",
    )
    замер.add_argument(
        "--dry-run", action="store_true", help="показать строки, ничего не записывая"
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Разобрать аргументы и выполнить команду либо отказать, назвав причину."""
    force_utf8_output()

    args = list(sys.argv[1:] if argv is None else argv)

    if not args:
        print(f"не указана команда; ожидается одна из: {_known()}", file=sys.stderr)
        return EXIT_USAGE

    имя = args[0]
    if имя in _ISSUE_BY_COMMAND:
        print(
            f"команда {имя!r} ещё не реализована: каркас проекта заведён, "
            f"поведение — нет (см. issue #{_ISSUE_BY_COMMAND[имя]})",
            file=sys.stderr,
        )
        return EXIT_USAGE
    if имя not in COMMANDS:
        print(
            f"неизвестная команда {имя!r}; ожидается одна из: {_known()}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    return _sample(build_parser().parse_args(args))


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
