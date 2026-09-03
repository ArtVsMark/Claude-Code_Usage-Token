"""Проверки выпуска: версия и содержимое дистрибутива (#12).

Публикация **необратима**. Имя и версия, ушедшие в индекс, не переигрываются:
занятую версию нельзя выпустить заново даже после удаления. Поэтому здесь всё,
что можно проверить до публикации, проверяется до неё.

## Что проверяется и почему именно это

**Согласованность версии.** Тег `v0.1.0`, метаданные пакета и то, что печатает
сам инструмент, обязаны совпадать. Расхождение не ломает ни сборку, ни
установку: пакет ставится, а `--version` называет не то, что опубликовано, — и
обнаруживается это уже у того, кто прислал отчёт о поведении несуществующей
версии.

**Содержимое дистрибутива, а не репозитория.** Вопрос не «что лежит в дереве»,
а «что уехало». Раскладка `src` и правила сборки легко дают пакет без модуля
или пакет с тестами внутри, и в репозитории это не видно вовсе.

**Маркер типизации.** `py.typed` обязан попасть в колесо: без него строгость
типов не уедет к тому, кто поставит пакет, — типы будут проигнорированы молча.

## Чего здесь нет

Проверки, что тег стоит на коммите из общей ветки. Это одна команда git
(`merge-base --is-ancestor`), и она живёт в прогоне: тащить сюда работу с
репозиторием значило бы заводить второй способ спросить то же самое.
"""

from __future__ import annotations

import argparse
import re
import sys
import zipfile
from collections.abc import Sequence
from pathlib import Path

from utf8_output import force_utf8_output

EXIT_FAILED = 1
EXIT_BROKEN = 2

#: Тег выпуска. Строго `vX.Y.Z`: свободная форма означала бы, что версию
#: пакета придётся угадывать разбором, а угаданная версия — та же ошибка, от
#: которой заведена эта проверка.
_TAG_RE = re.compile(r"^v(?P<version>\d+\.\d+\.\d+)$")

#: Файлы, без которых пакет неполон.
REQUIRED_IN_WHEEL: tuple[str, ...] = (
    "claude_code_usage/__init__.py",
    "claude_code_usage/cli.py",
    "claude_code_usage/py.typed",
)

#: Каталоги, которых в дистрибутиве быть не должно. Тесты и служебные скрипты
#: внутри колеса — не косметика: они уезжают к каждому, кто поставит пакет, и
#: попадают в его пространство имён.
FORBIDDEN_PREFIXES: tuple[str, ...] = ("tests/", "scripts/", "changelog.d/", ".github/")


class TagFormatError(ValueError):
    """Тег не той формы — версию из него не вывести."""


def version_from_tag(tag: str) -> str:
    """Вынуть версию из тега `vX.Y.Z`."""
    совпадение = _TAG_RE.match(tag.strip())
    if совпадение is None:
        raise TagFormatError(
            f"тег {tag!r} не вида vX.Y.Z — версию пакета из него не вывести, "
            "а угаданная версия это та же ошибка, от которой заведена проверка"
        )
    return совпадение.group("version")


def check_version(tag: str, package_version: str) -> list[str]:
    """Сверить тег с версией пакета."""
    try:
        версия_тега = version_from_tag(tag)
    except TagFormatError as exc:
        return [str(exc)]
    if версия_тега != package_version:
        return [
            f"тег обещает {версия_тега}, пакет объявляет {package_version}. "
            "Публикация необратима: разойдясь здесь, они разойдутся навсегда"
        ]
    return []


def wheel_names(path: Path) -> list[str]:
    """Что лежит внутри колеса."""
    with zipfile.ZipFile(path) as архив:
        return архив.namelist()


def _is_metadata(имя: str) -> bool:
    """`*.dist-info/METADATA` — и только он."""
    return имя.endswith(".dist-info/METADATA")


def metadata_version(path: Path) -> str | None:
    """Версия из METADATA колеса — то, что увидит устанавливающий."""
    with zipfile.ZipFile(path) as архив:
        for имя in архив.namelist():
            if _is_metadata(имя):
                текст = архив.read(имя).decode("utf-8", errors="replace")
                for строка in текст.splitlines():
                    if строка.startswith("Version:"):
                        return строка.split(":", 1)[1].strip()
    return None


def check_wheel(path: Path, package_version: str) -> list[str]:
    """Проверить, что уехало в колесе, а не что лежало в репозитории."""
    if not path.is_file():
        return [f"{path}: колеса нет — проверять нечего"]

    try:
        имена = wheel_names(path)
    except (OSError, zipfile.BadZipFile) as exc:
        return [f"{path}: колесо не читается — {exc}"]

    problems: list[str] = []

    недостающие = [нужно for нужно in REQUIRED_IN_WHEEL if нужно not in имена]
    if недостающие:
        problems.append(
            f"в колесе нет обязательного: {', '.join(недостающие)}. "
            "Раскладка src легко даёт пакет без модуля, и в репозитории это не "
            "видно вовсе"
        )

    лишние = sorted(
        имя
        for имя in имена
        if any(имя.startswith(префикс) for префикс in FORBIDDEN_PREFIXES)
    )
    if лишние:
        problems.append(
            f"в колесе лишнее: {', '.join(лишние[:5])}. Тесты и служебные "
            "скрипты уезжают к каждому, кто поставит пакет, и попадают в его "
            "пространство имён"
        )

    объявлено = metadata_version(path)
    if объявлено is None:
        problems.append(f"{path}: в колесе нет METADATA — версию не прочитать")
    elif объявлено != package_version:
        problems.append(
            f"METADATA колеса объявляет {объявлено}, пакет — {package_version}"
        )

    return problems


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8_output()

    парсер = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    парсер.add_argument("--tag", required=True, help="тег выпуска, вида v0.1.0")
    парсер.add_argument("--version", required=True, help="версия пакета")
    парсер.add_argument("--wheel", type=Path, help="собранное колесо")
    аргументы = парсер.parse_args(list(argv) if argv is not None else None)

    problems = check_version(аргументы.tag, аргументы.version)
    if аргументы.wheel is not None:
        problems += check_wheel(аргументы.wheel, аргументы.version)

    for проблема in problems:
        print(f"::error::{проблема}")

    if problems:
        print(
            f"\nвыпуск не готов: {len(problems)}. Публикация необратима, "
            "поэтому проверка идёт до неё",
            file=sys.stderr,
        )
        return EXIT_FAILED

    хвост = "" if аргументы.wheel is None else f", колесо {аргументы.wheel.name}"
    print(f"выпуск готов: тег {аргументы.tag} и пакет сходятся{хвост}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
