"""Версия проекта: считается по истории, а не правится на глазок.

## Схема — не SemVer

Взята у соседей ([Engineering-Incidents-Playbook], а туда — у грейдера) и
держит инвариант **каждый тег = `vX.Y.0`**:

    MAJOR . MINOR . PATCH
      │       │       └─ +1 на принятое изменение; обнуляется при выпуске
      │       └───────── +1 ВСЕГДА при выпуске
      └───────────────── только фундаментальное

Привычные правила SemVer её ломают, поэтому по памяти их применять нельзя.
PATCH-тегов не существует: `0.1.12` читается как «двенадцать изменений после
`v0.1.0`», а не «двенадцатый патч-выпуск» — выпуска с таким номером нет и не
будет.

**Что здесь значит 1.0.** Решение владельца 2026-09-02: `1.0` ставится, когда
работает весь эпик #1 — замер, отчёт и калибровка собраны и шкала хоть раз
откалибрована на живых данных. Критерий проверяемый, а не «когда покажется
готовым». До тех пор идут `0.2`, `0.3`, `0.4` и публикации на PyPI нет.

## Форм две, и это названо, а не сглажено

У соседа форма одна: пакета нет, публиковать нечего. Здесь пакет есть, и формы
две:

* **выпущенная** — литерал `__version__` в `src/claude_code_usage/__init__.py`.
  Её несёт колесо и её увидит тот, кто поставит пакет. Равна последнему тегу
  ровно, и это держит :func:`check`;
* **счётная** — `X.Y.N` из истории. Говорит, где мы сейчас относительно
  выпуска, и в метаданные пакета не попадает никогда.

Смешивать их нельзя: счётная меняется с каждым мержем, а выпущенная — только
при выпуске. Сгладить разницу значило бы объявить в колесе номер, которого
никто не выпускал.

## Почему счёт, а не вывод версии из тегов

`docs/release.md` записал отказ от `hatch-vcs` так: облачное окно клонирует
мелко, версия молча стала бы `0.0.1.dev5+g…`, и опубликовалось бы именно это.
Возражение было про **молча**, а не про «из тегов», и сосед это показал: у его
скрипта есть третий исход — не видно тега схемы, значит версия недостоверна, и
он говорит, что делать, вместо правдоподобной цифры.

Здесь так же: :func:`counted` отдаёт `None`, а `main` выходит кодом 2 и
называет `git fetch --tags`.

## Почему считаются сущности, а не рёбра графа

Топологическая формула меряет форму истории, а форма зависит от окна: `git
pull` мержем уводит пришедшее с площадки во второй родитель. Поэтому считаются
номера изменений — множество гасит и двойной учёт, когда изменение попало в
историю дважды.

Безномерные берутся **только** с first-parent линии: иначе внутренние коммиты
слитой ветки считались бы поштучно, и дробление завышало бы счёт.

[Engineering-Incidents-Playbook]: https://github.com/ArtVsMark/Engineering-Incidents-Playbook
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

EXIT_FAILED = 1

#: Версия недостоверна: тега схемы не видно. Отдельный код — это не «проверка
#: нашла проблему», а «проверять не на чем».
EXIT_UNKNOWN = 2

#: Тег схемы. Форма сверяется дважды: маской для `git` и регуляркой для нас.
TAG_GLOB = "v[0-9]*.[0-9]*.[0-9]*"
TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

#: Номер изменения в теме коммита: `(#58)` от уплотнённого мержа либо
#: `Merge pull request #58` от обычного.
PR_RE = re.compile(r"\(#(\d+)\)")
MERGE_PR_RE = re.compile(r"^Merge pull request #(\d+)\b")

#: Склейка от `git pull`: изменением не является.
SYNC_MERGE_RE = re.compile(r"^Merge (branch|remote-tracking branch) ")

#: Где живёт выпущенная версия. Одно место — `pyproject.toml` берёт её оттуда.
VERSION_PATH = Path("src") / "claude_code_usage" / "__init__.py"
_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def _git(*args: str, root: Path = ROOT) -> str | None:
    """stdout команды без хвоста; `None` при любой неудаче."""
    try:
        done = subprocess.run(
            ("git", "-C", str(root), *args),
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError:
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def _subjects(
    rev_range: str, *, first_parent: bool = False, root: Path = ROOT
) -> list[str]:
    args = ["log", "--pretty=%s"]
    if first_parent:
        args.append("--first-parent")
    out = _git(*args, rev_range, root=root)
    return [line for line in out.split("\n") if line] if out else []


def pr_numbers(subjects: Sequence[str]) -> set[str]:
    """Номера изменений из тем коммитов."""
    найдено: set[str] = set()
    for тема in subjects:
        найдено.update(PR_RE.findall(тема))
        слияние = MERGE_PR_RE.match(тема)
        if слияние:
            найдено.add(слияние.group(1))
    return найдено


def countable(subject: str) -> bool:
    """Считается ли безномерная тема. Склейка `git pull` — нет."""
    return not SYNC_MERGE_RE.match(subject)


def accepted_since(rev_range: str, *, root: Path = ROOT) -> int:
    """Сколько изменений принято в диапазоне.

    Номера — по всей истории диапазона: при `git pull` мержем пришедшее лежит
    во втором родителе. Безномерные — только с first-parent линии, иначе
    внутренние коммиты слитой ветки считались бы поштучно.
    """
    номера = pr_numbers(_subjects(rev_range, root=root))
    безномерные = [
        тема
        for тема in _subjects(rev_range, first_parent=True, root=root)
        if not PR_RE.search(тема) and not MERGE_PR_RE.match(тема) and countable(тема)
    ]
    return len(номера) + len(безномерные)


def latest_tag(root: Path = ROOT) -> str | None:
    """Ближайший тег схемы или `None`."""
    тег = _git("describe", "--tags", "--abbrev=0", "--match", TAG_GLOB, root=root)
    return тег if тег and TAG_RE.match(тег) else None


def released_version(root: Path = ROOT) -> str:
    """Выпущенная версия — литерал, который несёт колесо."""
    текст = (root / VERSION_PATH).read_text(encoding="utf-8")
    найдено = _VERSION_RE.search(текст)
    if not найдено:
        raise ValueError(f'в {VERSION_PATH} нет строки __version__ = "…"')
    return найдено.group(1)


def counted(root: Path = ROOT) -> tuple[str, str, int] | None:
    """(тег, счётная «X.Y.N», N) либо `None`, если тега схемы не видно.

    `None` — третий исход, а не отказ: так выглядит мелкий клон облачного окна
    и `actions/checkout` без `fetch-depth: 0`. Правдоподобная цифра здесь была
    бы хуже молчания, потому что выглядела бы свежей.
    """
    тег = latest_tag(root)
    if тег is None:
        return None
    if not (root / VERSION_PATH).is_file():
        return None
    major, minor, _ = TAG_RE.match(тег).groups()  # type: ignore[union-attr]
    сколько = accepted_since(f"{тег}..HEAD", root=root)
    return тег, f"{major}.{minor}.{сколько}", сколько


def check(root: Path = ROOT) -> list[str]:
    """Расходится ли выпущенная версия с последним тегом.

    Между выпусками литерал обязан равняться тегу: инвариант схемы — каждый тег
    `vX.Y.0`, а значит выпущенная версия и есть последний тег. Расхождение
    означает либо поднятую и не выпущенную версию, либо забытый подъём.
    """
    тег = latest_tag(root)
    if тег is None:
        return []
    выпущенная = released_version(root)
    if тег.lstrip("v") != выпущенная:
        return [
            f"выпущенная версия {выпущенная} не равна последнему тегу {тег}. "
            "Инвариант схемы — каждый тег vX.Y.0, поэтому между выпусками "
            "литерал и тег совпадают; см. docs/versioning.md"
        ]
    return []


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Версия по схеме MAJOR.MINOR.PATCH")
    parser.add_argument("--check", action="store_true", help="сверить литерал с тегом")
    parser.add_argument("--released", action="store_true", help="только выпущенную")
    аргументы = parser.parse_args(argv)

    if аргументы.released:
        print(released_version(ROOT))
        return 0

    сведения = counted(ROOT)
    if сведения is None:
        print(
            "версия недостоверна: тега схемы vX.Y.Z не видно. Так клонирует "
            "облачное окно и actions/checkout без fetch-depth: 0 — правдоподобная "
            "цифра тут была бы хуже отказа. Выполните: git fetch --tags",
            file=sys.stderr,
        )
        return EXIT_UNKNOWN

    тег, полная, сколько = сведения
    if аргументы.check:
        беды = check(ROOT)
        if беды:
            for беда in беды:
                print(беда, file=sys.stderr)
            return EXIT_FAILED
        print(
            f"версия {released_version(ROOT)} ≡ тег {тег}; с выпуска принято {сколько}"
        )
        return 0

    print(полная)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
