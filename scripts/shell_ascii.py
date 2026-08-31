"""Имена в shell — только ASCII (#34).

Внутренний язык проекта русский: проза, docstring'и, тексты ошибок и даже
идентификаторы Python. На bash это **не распространяется**, и правило из
`CLAUDE.md` — «английским остаётся синтаксис» — про имена переменных shell
молчит. Соблазн назвать переменную `файлы` возвращается при каждом новом
workflow.

## Почему одной ошибки мало, чтобы научиться

Ошибка проявляется двумя способами, и второй опаснее.

**Громко.** `файлы=$(git diff …)` bash разбирает не как присваивание, а как
**имя команды**: прогон падает с кодом 127. Такое чинится само собой — красное
видно.

**Тихо.** Переменная окружения с не-ASCII именем создаётся штатно, но `$ТИП` в
bash **не раскрывается вовсе**: парсер требует ASCII-идентификатор. Подстановка
выходит пустой, условие всегда даёт одну ветку, и гейт выключается молча,
оставаясь зелёным. Он отработал, ничего не проверив.

## Что проверяется

Три места, где не-ASCII имя ломает поведение:

1. присваивание в блоке `run:` — `имя=значение`;
2. ключ в блоке `env:` — переменная создастся, но не прочитается;
3. подстановка `$имя` или `${имя}` где угодно в файле — она не раскроется.

**Проза не трогается.** Комментарии, `name:`, тексты `echo` остаются русскими:
правило про идентификаторы, а не про язык.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

EXIT_FAILED = 1

#: Присваивание в начале строки, возможно с `export`/`local`/`declare`.
#: Токен обязан примыкать к `=` вплотную: `echo "к=в"` присваиванием не
#: является, и заворачивать его было бы ложным отказом.
_ASSIGN = re.compile(r"^\s*(?:export\s+|local\s+|declare\s+(?:-\w+\s+)?)?([^\s=]+)=")

#: Подстановка `$имя` и `${имя}`. Выражения площадки `${{ … }}` сюда не
#: попадают: после `${` идёт `{`, а он не годится в имя.
_SUBST = re.compile(r"\$\{?([^\s{}$\"'`;|&()\[\]=/:,.\\*+-]+)")

_NON_ASCII = re.compile(r"[^\x00-\x7f]")


@dataclass(frozen=True)
class Finding:
    """Одна находка: файл, строка и что именно не так."""

    path: str
    line: int
    message: str

    def __str__(self) -> str:
        return f"{self.path}:{self.line}: {self.message}"


#: Значения `run:`, за которыми тело идёт следующими строками, а не на этой.
_BLOCK_SCALARS = frozenset({"", "|", ">", "|-", ">-", "|+", ">+"})


def _blocks(lines: Sequence[str], keyword: str) -> dict[int, str]:
    """Строки блока `keyword:` и их shell-часть.

    Блок определяется отступом: он длится, пока отступ строки больше отступа
    самого ключа. Пустые строки блок не обрывают — иначе `run:` из двух абзацев
    считался бы двумя блоками, и второй остался бы непроверенным.

    Значение — не строка целиком, а её shell-часть. У однострочного
    `run: имя=значение` присваивание стоит в середине строки, и проверка по
    началу строки его не увидела бы вовсе: гейт молчал бы ровно на той форме,
    которую пишут второпях.
    """
    внутри: dict[int, str] = {}
    начало: int | None = None
    for номер, строка in enumerate(lines):
        if not строка.strip():
            if начало is not None:
                внутри[номер] = строка
            continue
        отступ = len(строка) - len(строка.lstrip())
        if начало is not None and отступ > начало:
            внутри[номер] = строка
            continue
        начало = None
        совпадение = re.match(rf"^(\s*)-?\s*{keyword}:", строка)
        if совпадение:
            начало = len(совпадение.group(1))
            хвост = строка.split(":", 1)[1]
            if хвост.strip() not in _BLOCK_SCALARS:
                внутри[номер] = хвост
    return внутри


def check_text(text: str, path: str) -> list[Finding]:
    """Найти не-ASCII идентификаторы shell в одном файле workflow."""
    lines = text.splitlines()
    findings: list[Finding] = []

    в_run = _blocks(lines, "run")
    в_env = _blocks(lines, "env")

    for номер, строка in enumerate(lines):
        голая = строка.strip()
        if голая.startswith("#"):
            continue

        if номер in в_run:
            совпадение = _ASSIGN.match(в_run[номер])
            if совпадение and _NON_ASCII.search(совпадение.group(1)):
                findings.append(
                    Finding(
                        path,
                        номер + 1,
                        f"присваивание `{совпадение.group(1)}=` — bash разберёт "
                        "это как имя команды и упадёт с кодом 127",
                    )
                )

        if номер in в_env:
            ключ = re.match(r"^\s*([^\s:#]+):", строка)
            if ключ and _NON_ASCII.search(ключ.group(1)):
                findings.append(
                    Finding(
                        path,
                        номер + 1,
                        f"ключ env `{ключ.group(1)}` — переменная создастся, но "
                        "`$имя` в bash не раскроется: гейт выключится молча, "
                        "оставшись зелёным",
                    )
                )

        for имя in _SUBST.findall(строка):
            if _NON_ASCII.search(имя):
                findings.append(
                    Finding(
                        path,
                        номер + 1,
                        f"подстановка `${имя}` не раскроется — bash требует "
                        "ASCII-идентификатор, и подстановка выйдет пустой",
                    )
                )

    return findings


def workflow_files(root: Path) -> list[Path]:
    """Файлы workflow проекта. Отдельной функцией — чтобы охват был назван."""
    каталог = root / ".github" / "workflows"
    return sorted(каталог.glob("*.yml")) + sorted(каталог.glob("*.yaml"))


def check_workflows(root: Path) -> list[Finding]:
    """Пройти по всем workflow проекта.

    Пустой список файлов — не «чисто», а «проверять нечего»: гейт, не нашедший
    предмета, обязан сказать об этом, иначе переезд каталога тихо его отключит.
    """
    каталог = root / ".github" / "workflows"
    файлы = workflow_files(root)
    if not файлы:
        return [
            Finding(
                str(каталог),
                0,
                "workflow не найдено — гейт остался без предмета проверки",
            )
        ]

    findings: list[Finding] = []
    for путь in файлы:
        findings += check_text(
            путь.read_text(encoding="utf-8"), str(путь.relative_to(root))
        )
    return findings


def main(argv: Sequence[str] | None = None) -> int:
    корень = Path(argv[0]) if argv else Path(__file__).resolve().parent.parent
    findings = check_workflows(корень)
    for находка in findings:
        print(f"::error::{находка}")
    if findings:
        print(f"\nимена shell не латиницей: {len(findings)}", file=sys.stderr)
        return EXIT_FAILED
    print("имена shell в порядке: только ASCII")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
