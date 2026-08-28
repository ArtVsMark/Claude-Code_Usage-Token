"""Точка входа команды ``claude-code-usage-meter``.

Ни одна команда пока не реализована: заведён каркас, поведения нет. Поэтому
единственная задача этого модуля — **отказать громко**.

Требование сформулировано ролью 🧪 Тестировщика в ``docs/roles.md``: ненулевой
код возврата и сообщение, которое называет, что именно не вышло. Точка входа,
молча возвращающая ноль, соврала бы дважды — человеку и гейту.
"""

from __future__ import annotations

import sys
from collections.abc import Sequence

#: Команды из ``docs/spec.md``, § «Что инструмент делает». Перечислены здесь,
#: чтобы отказ мог отличить «ещё не написано» от «такого не бывает»: это
#: разные ответы, и человеку нужен разный следующий шаг.
COMMANDS: tuple[str, ...] = ("sample", "report", "calibrate")

#: Код возврата при любом отказе. Ненулевой — обязательное требование;
#: конкретно 2 отделяет ошибку вызова от будущих ошибок работы.
EXIT_USAGE = 2

_ISSUE_BY_COMMAND = {
    "sample": 2,
    "report": 1,
    "calibrate": 1,
}


def _known() -> str:
    return ", ".join(COMMANDS)


def main(argv: Sequence[str] | None = None) -> int:
    """Разобрать аргументы и отказать, назвав причину.

    Возвращает код возврата, а не вызывает :func:`sys.exit`, чтобы отказ можно
    было проверить тестом, не перехватывая исключение.
    """
    args = list(sys.argv[1:] if argv is None else argv)

    if not args:
        print(
            f"не указана команда; ожидается одна из: {_known()}",
            file=sys.stderr,
        )
        return EXIT_USAGE

    name = args[0]

    if name in COMMANDS:
        print(
            f"команда {name!r} ещё не реализована: каркас проекта заведён, "
            f"поведение — нет (см. issue #{_ISSUE_BY_COMMAND[name]})",
            file=sys.stderr,
        )
        return EXIT_USAGE

    print(
        f"неизвестная команда {name!r}; ожидается одна из: {_known()}",
        file=sys.stderr,
    )
    return EXIT_USAGE


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
