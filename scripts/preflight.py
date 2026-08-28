"""Одна команда перед коммитом вместо чек-листа из четырёх строк.

Чек-лист требует помнить; команда — исполняется. Разбор соседнего проекта
показал, что **девять инцидентов из одиннадцати были не незнанием правила, а
его пропуском**, поэтому здесь чек-лист переписан в команду, а не дополнен ещё
одним пунктом (#5).

Два требования к поведению, оба обязательные:

* **ненулевой код возврата при любом отказе** — иначе гейт не гейт;
* **вывод называет, что именно не прошло**, а не «упало». Отказ, не
  называющий себя, заставляет запускать всё заново руками, то есть возвращает
  ровно тот чек-лист, который команда заменяет.

Проверки идут **все**, а не до первого отказа: две поломки за один прогон
дешевле двух прогонов, а «первая красная» скрывает вторую.
"""

from __future__ import annotations

import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Ненулевой код при любом отказе. Конкретное значение неважно, важно, что
#: оно не ноль; 1 отделяет «проверки нашли проблему» от 2 — «прогнать не вышло».
EXIT_FAILED = 1
EXIT_BROKEN = 2


@dataclass(frozen=True)
class Check:
    """Одна проверка: как её зовут по-русски и чем она запускается."""

    name: str
    argv: tuple[str, ...]


def checks() -> tuple[Check, ...]:
    """Проверки чек-листа «Перед PR», по одной на строку чек-листа.

    Запускаются через ``sys.executable -m``, а не по имени в ``PATH``: у окна
    может не быть ``ruff`` в ``PATH``, зато интерпретатор известен точно. Это
    же снимает разницу между Windows и Linux, где ``PATH`` устроен по-разному.
    """
    py = sys.executable
    return (
        Check("тесты (весь набор)", (py, "-m", "pytest")),
        Check("ruff check", (py, "-m", "ruff", "check", ".")),
        Check("ruff format --check", (py, "-m", "ruff", "format", "--check", ".")),
        Check("mypy", (py, "-m", "mypy")),
    )


# ── проверка на секреты ───────────────────────────────────────────────────
#
# Шаблоны собраны так, чтобы **не находить сами себя**: литерал разбит
# символьным классом (``gh[p]_`` вместо ``ghp_``), и дополнительно требуется
# длина, которой в прозе не бывает. Без этого первый же прогон покраснел бы на
# собственном исходнике, и проверку отключили бы как ложную.
#
# Список намеренно короткий: это не замена #7, где заводится белый список
# полей замера. Здесь ловится грубое — то, что вообще не должно оказаться в
# репозитории с кодом.
_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("токен GitHub", re.compile(r"gh[pousr]_[A-Za-z0-9]{36}")),
    ("токен GitHub (fine-grained)", re.compile(r"github[_]pat_[A-Za-z0-9_]{60,}")),
    ("ключ Anthropic", re.compile(r"sk[-]ant-[A-Za-z0-9_-]{24,}")),
    ("ключ AWS", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("закрытый ключ", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
)


def tracked_files() -> list[Path]:
    """Файлы, которые уедут в коммит: отслеживаемые плюс новые незаигнорённые.

    Именно этот набор, а не рабочее дерево целиком: ``preflight`` запускается
    **перед** коммитом, и вопрос у него — что попадёт в репозиторий.

    ``-z`` обязателен, и это не мелочь оформления. Без него ``git ls-files``
    экранирует имена с не-ASCII символами (``"\321\203..."``), путь не
    разрешается, и файл **молча** выпадает из проверки. Внутренний язык
    проекта русский, поэтому кириллица в имени файла — обычное дело, а не
    экзотика: без ``-z`` проверка на секреты была бы слепа ровно там, где
    вероятнее всего пишут.
    """
    out = subprocess.run(
        ("git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"),
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    ).stdout
    return [ROOT / line for line in out.split("\0") if line]


@dataclass(frozen=True)
class ScanResult:
    """Находки и **охват**: сколько файлов просмотрено, сколько пропущено.

    Охват — часть вывода, а не отладочная информация. «Чисто» без числа
    просмотренных файлов неотличимо от «ничего не проверяли», а именно так
    выглядел этот модуль до того, как выяснилось, что не-ASCII имена молча
    выпадали из перечисления.
    """

    findings: list[str]
    examined: int
    skipped: int


def scan_for_secrets(paths: Iterable[Path]) -> ScanResult:
    """Найти секреты и файлы замеров; вернуть находки вместе с охватом.

    Находкой считается и **файл замеров**: `*.jsonl` в этом репозитории не
    бывает по определению — данные живут в отдельном приватном хранилище, и
    «обезличенный» файл замеров тоже нельзя, это чужая история работы.
    """
    findings: list[str] = []
    examined = 0
    skipped = 0
    for path in paths:
        rel = path.relative_to(ROOT).as_posix()

        if not path.is_file():
            # Файл исчез между перечислением и чтением — гонка, а не находка.
            skipped += 1
            continue

        if path.suffix == ".jsonl":
            examined += 1
            findings.append(
                f"{rel}: файл замеров в репозитории кода. "
                "Замеры живут в отдельном приватном хранилище — см. SECURITY.md"
            )
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            # Двоичный файл: искать шаблоны в нём нечем. Пропуск **считается**
            # и попадает в вывод — молчаливый пропуск однажды уже спрятал
            # дефект перечисления.
            skipped += 1
            continue

        examined += 1

        for label, pattern in _SECRET_PATTERNS:
            match = pattern.search(text)
            if match is None:
                continue
            line = text.count("\n", 0, match.start()) + 1
            findings.append(f"{rel}:{line}: похоже на {label}")

    return ScanResult(findings=findings, examined=examined, skipped=skipped)


# ── прогон ────────────────────────────────────────────────────────────────


def run_check(check: Check) -> tuple[bool, str]:
    """Прогнать одну проверку. Вернуть «прошла ли» и её вывод."""
    proc = subprocess.run(
        check.argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return proc.returncode == 0, (proc.stdout + proc.stderr).strip()


def report(passed: Sequence[str], failed: Sequence[tuple[str, str]]) -> str:
    """Собрать итог так, чтобы он называл отказавшее по имени."""
    lines = [f"  ✓ {name}" for name in passed]
    lines += [f"  ✗ {name}" for name, _ in failed]

    if not failed:
        return "\n".join([*lines, "", f"всё чисто: проверок {len(passed)}"])

    names = ", ".join(name for name, _ in failed)
    return "\n".join(
        [
            *lines,
            "",
            f"не прошло: {names}",
            f"(проверок всего {len(passed) + len(failed)}, отказов {len(failed)})",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Прогнать всё и вернуть ненулевой код, если хоть что-то не прошло."""
    if argv:
        print(
            f"preflight не принимает аргументов, получено: {list(argv)}",
            file=sys.stderr,
        )
        return EXIT_BROKEN

    passed: list[str] = []
    failed: list[tuple[str, str]] = []

    for check in checks():
        ok, output = run_check(check)
        (passed.append(check.name) if ok else failed.append((check.name, output)))

    try:
        scan = scan_for_secrets(tracked_files())
    except (OSError, subprocess.CalledProcessError) as exc:
        # Отдельный код возврата: «проверку не удалось прогнать» — это не то же
        # самое, что «проверка нашла секрет», и путать их нельзя.
        print(
            f"preflight не отработал: не удалось перечислить файлы — {exc}",
            file=sys.stderr,
        )
        return EXIT_BROKEN

    name = (
        f"секреты и замеры в диффе "
        f"(просмотрено {scan.examined}, пропущено двоичных {scan.skipped})"
    )
    if scan.findings:
        failed.append((name, "\n".join(scan.findings)))
    else:
        passed.append(name)

    for failed_name, output in failed:
        print(f"── {failed_name} ──", file=sys.stderr)
        if output:
            print(output, file=sys.stderr)
        print(file=sys.stderr)

    print(report(passed, failed), file=sys.stderr)
    return EXIT_FAILED if failed else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
