"""Текст доставляется файлом или закавыченным heredoc, а не экранированием (013).

Между окном и файлом стоит оболочка, и она **интерпретирует** содержимое: в
heredoc с НЕзакавыченным разделителем — `<<EOF` вместо `<<'EOF'` — раскрываются
`$переменные`, `` `подстановки` `` и `\\`-последовательности. Текст, который
пишут, и текст, который ложится на диск, расходятся молча: `$HOME` исчезает,
`\\n` превращается в перевод строки, а в прозе на русском кавычка и доллар
встречаются чаще, чем кажется.

## Что проверяется и почему именно это

Предмет — блоки `run:` в прогонах: это единственное место в дереве, где
оболочка пишет файлы. Ищется открывающий heredoc, чей разделитель не взят в
кавычки. Закавыченный (`<<'EOF'`, `<<"EOF"`) оболочка не трогает вовсе, и это
ровно то поведение, ради которого приём и выбран.

## Чего гейт не ловит

**Ручное экранирование в аргументе.** `--body "…\\"…\\""` он не судит: отличить
законную кавычку в тексте от экранирования, которое разъедется, разбором
нельзя. Держит это приём — текст доставляется `--body-file`, — а не проверка,
и граница названа здесь.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from utf8_output import force_utf8_output

EXIT_FAILED = 1
EXIT_BROKEN = 2

WORKFLOWS = Path(".github") / "workflows"

#: Открывающий heredoc. Кавычки у разделителя — то, что и проверяется.
_HEREDOC = re.compile(r"<<-?\s*(?P<кавычка>['\"]?)(?P<имя>[A-Za-z_][A-Za-z0-9_]*)")


@dataclass(frozen=True)
class Находка:
    файл: str
    строка: int
    имя: str

    def __str__(self) -> str:
        return (
            f"{self.файл}:{self.строка}: heredoc `<<{self.имя}` без кавычек у "
            "разделителя. Оболочка раскроет $переменные и обратные слэши в теле, "
            f"и записанное разойдётся с написанным молча. Нужно `<<'{self.имя}'`"
        )


@dataclass(frozen=True)
class Итог:
    находки: list[Находка]
    прогонов: int
    heredoc_ов: int


def проверить_текст(текст: str, путь: str) -> tuple[list[Находка], int]:
    находки: list[Находка] = []
    всего = 0
    for номер, строка in enumerate(текст.splitlines(), 1):
        for совпало in _HEREDOC.finditer(строка):
            всего += 1
            if not совпало.group("кавычка"):
                находки.append(Находка(путь, номер, совпало.group("имя")))
    return находки, всего


def проверить(корень: Path) -> Итог:
    каталог = корень / WORKFLOWS
    if not каталог.is_dir():
        return Итог([], 0, 0)

    находки: list[Находка] = []
    прогонов = 0
    heredoc_ов = 0
    for файл in sorted(каталог.iterdir()):
        if файл.suffix not in {".yml", ".yaml"}:
            continue
        прогонов += 1
        свои, всего = проверить_текст(
            файл.read_text(encoding="utf-8"), (WORKFLOWS / файл.name).as_posix()
        )
        находки.extend(свои)
        heredoc_ов += всего
    return Итог(находки, прогонов, heredoc_ов)


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8_output()

    аргументы = list(sys.argv[1:] if argv is None else argv)
    корень = Path(аргументы[0]) if аргументы else Path(__file__).resolve().parent.parent

    try:
        итог = проверить(корень)
    except OSError as отказ:
        print(f"не отработал: {отказ}", file=sys.stderr)
        return EXIT_BROKEN

    for находка in итог.находки:
        print(находка, file=sys.stderr)
    print(f"прогонов {итог.прогонов}, heredoc'ов {итог.heredoc_ов}")
    return EXIT_FAILED if итог.находки else 0


if __name__ == "__main__":
    raise SystemExit(main())
