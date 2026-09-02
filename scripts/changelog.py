"""Changelog фрагментами: запись едет вместе с изменением (#10).

## Почему не один файл

Две причины, обе проверены.

**Конфликты.** Правка одного `CHANGELOG.md` всеми ветками подряд даёт конфликт
на каждом втором PR — причём в месте, которое сливать бессмысленно: строки
независимы, а git об этом не знает.

**Правдивость.** Запись, написанная после релиза, пишется по памяти и врёт.
Запись, приезжающая вместе с изменением, описывает то, что автор только что
сделал и ещё помнит.

## Формат

Один файл на запись, имя `changelog.d/<номер задачи>.<вид>.md`::

    changelog.d/10.added.md

Номер задачи — в **имени**, а не в тексте: так его можно сверить с телом PR, не
разбирая прозу. Вид — из `KINDS`; он же задаёт раздел собранного `CHANGELOG.md`.

Содержимое — одна-две строки по-русски. `CHANGELOG.md` и release notes
публикуются наружу, значит должны быть на языке проекта.

## Исключение для записей из одних идентификаторов

Объявляется **строкой-маркером**, а не угадывается. Запись вида «`ruff` поднят
до 0.6» кириллицы не содержит по природе, и эвристика «нет кириллицы — значит
не переведено» краснела бы на ней. Эвристики по длине здесь тоже нет: короткая
русская запись законна, а длинная английская — нет, и длина об этом не говорит
ничего.

## Строгость по месту

На PR — предупреждение: запись ещё правится, и красное здесь приучало бы читать
красное как фон. На релизе — отказ: публикация необратима.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: Каталог фрагментов. Пустой — законное состояние: «менять нечего».
FRAGMENTS = "changelog.d"

#: Виды записей и их разделы в собранном документе. Список закрытый: свободный
#: вид означал бы раздел, которого нет в шаблоне, и запись потерялась бы при
#: сборке молча.
KINDS: dict[str, str] = {
    "added": "Добавлено",
    "changed": "Изменено",
    "fixed": "Исправлено",
    "removed": "Убрано",
}

#: Маркер записи, которой кириллица не нужна по природе. Явный, а не
#: угадываемый: «`ruff` поднят до 0.6» — законная запись без единой русской
#: буквы, и эвристика завернула бы её.
IDENTIFIERS_ONLY = "<!-- только идентификаторы -->"

#: Типы PR, у которых запись обязательна. `documentation` не меняет поведения,
#: и требовать запись от правки документа значило бы разводить шум, в котором
#: потеряется настоящая запись.
BEHAVIOUR_TYPES = frozenset({"bug", "enhancement"})

EXIT_FAILED = 1
EXIT_BROKEN = 2

_CYRILLIC = re.compile("[Ѐ-ӿ]")
_NAME_RE = re.compile(r"^(?P<issue>\d+)\.(?P<kind>[a-z]+)\.md$")


@dataclass(frozen=True)
class Fragment:
    """Одна запись changelog."""

    path: Path
    issue: int
    kind: str
    text: str

    @property
    def identifiers_only(self) -> bool:
        return IDENTIFIERS_ONLY in self.text

    @property
    def body(self) -> str:
        """Текст без служебного маркера."""
        return self.text.replace(IDENTIFIERS_ONLY, "").strip()


def parse(path: Path) -> Fragment | str:
    """Разобрать файл фрагмента. Строка в ответе — причина, по которой не вышло."""
    совпадение = _NAME_RE.match(path.name)
    if совпадение is None:
        виды = ", ".join(sorted(KINDS))
        return (
            f"{FRAGMENTS}/{path.name}: имя не по формату "
            f"«<номер задачи>.<вид>.md», вид — один из: {виды}"
        )
    вид = совпадение.group("kind")
    if вид not in KINDS:
        return (
            f"{FRAGMENTS}/{path.name}: вид «{вид}» неизвестен. Заведённые — "
            f"{', '.join(sorted(KINDS))}; запись неизвестного вида потерялась бы "
            "при сборке молча"
        )
    try:
        текст = path.read_text(encoding="utf-8")
    except OSError as exc:
        return f"{FRAGMENTS}/{path.name}: не прочитан — {exc}"
    if not текст.strip():
        return f"{FRAGMENTS}/{path.name}: пустая запись — то же, что её отсутствие"
    return Fragment(
        path=path, issue=int(совпадение.group("issue")), kind=вид, text=текст
    )


def collect(root: Path) -> tuple[list[Fragment], list[str]]:
    """Собрать все фрагменты и претензии к ним."""
    каталог = root / FRAGMENTS
    фрагменты: list[Fragment] = []
    претензии: list[str] = []
    if not каталог.is_dir():
        return фрагменты, [f"{FRAGMENTS}: каталога нет — собирать не из чего"]

    for путь in sorted(каталог.glob("*.md")):
        if путь.name == "README.md":
            continue
        разобрано = parse(путь)
        if isinstance(разобрано, str):
            претензии.append(разобрано)
        else:
            фрагменты.append(разобрано)
    return фрагменты, претензии


def language_warnings(фрагменты: Sequence[Fragment]) -> list[str]:
    """Записи без кириллицы, не объявившие себя записями из идентификаторов."""
    замечания: list[str] = []
    for фрагмент in фрагменты:
        if фрагмент.identifiers_only:
            continue
        if _CYRILLIC.search(фрагмент.body) is None:
            замечания.append(
                f"{FRAGMENTS}/{фрагмент.path.name}: в записи нет ни одной русской "
                "буквы. CHANGELOG публикуется наружу и пишется на языке проекта; "
                f"если запись состоит из одних идентификаторов, объявите это "
                f"строкой «{IDENTIFIERS_ONLY}»"
            )
    return замечания


def render(фрагменты: Sequence[Fragment], version: str) -> str:
    """Собрать раздел `CHANGELOG.md` из фрагментов.

    Порядок внутри раздела — по номеру задачи, а не по имени файла: имя это
    номер и вид, и сортировка строкой поставила бы #10 перед #2.
    """
    строки = [f"## {version}", ""]
    for вид, заголовок in KINDS.items():
        свои = sorted((ф for ф in фрагменты if ф.kind == вид), key=lambda ф: ф.issue)
        if not свои:
            continue
        строки += [f"### {заголовок}", ""]
        for фрагмент in свои:
            текст = " ".join(фрагмент.body.split())
            строки.append(f"- {текст} (#{фрагмент.issue})")
        строки.append("")
    return "\n".join(строки).rstrip() + "\n"


#: Свод — накопленный журнал. Фрагменты складываются в него при подготовке
#: версии и на этом расходуются: иначе заметки следующего выпуска повторили бы
#: прошлые целиком (#49).
JOURNAL = "CHANGELOG.md"

#: Шапка свода. Пишется один раз, при первом складывании.
JOURNAL_HEAD = (
    "# Журнал изменений\n"
    "\n"
    "Разделы складываются из фрагментов `changelog.d/` при подготовке версии —\n"
    "см. [`docs/release.md`](docs/release.md). Руками сюда не пишут: запись\n"
    "приезжает вместе с изменением, а не сочиняется после выпуска.\n"
)

_SECTION_RE = re.compile(r"^## (?P<version>\S+)\s*$", re.MULTILINE)


def section(journal: str, version: str) -> str | None:
    """Раздел свода для версии — или `None`, если его нет.

    `None` при выпуске означает отказ: раздела нет — значит фрагменты не
    сложены, и заметки собрались бы из чужих записей.
    """
    границы = list(_SECTION_RE.finditer(journal))
    for номер, начало in enumerate(границы):
        if начало.group("version") != version:
            continue
        конец = границы[номер + 1].start() if номер + 1 < len(границы) else len(journal)
        return journal[начало.start() : конец].rstrip() + "\n"
    return None


def fold(root: Path, фрагменты: Sequence[Fragment], version: str) -> tuple[str, int]:
    """Сложить фрагменты в свод и израсходовать их.

    Возвращает новый текст свода и число израсходованных файлов. Сам файл
    здесь не пишется: решение «писать ли» принимает вызывающий, а функция
    остаётся проверяемой на подделанном дереве.
    """
    путь = root / JOURNAL
    прежний = путь.read_text(encoding="utf-8") if путь.is_file() else JOURNAL_HEAD
    if section(прежний, version) is not None:
        raise ValueError(
            f"раздел {version} в {JOURNAL} уже есть — складывать второй раз "
            "значит удвоить записи"
        )
    шапка, _, хвост = прежний.partition("## ")
    свежий = render(фрагменты, version)
    собранный = шапка.rstrip() + "\n\n" + свежий
    if хвост:
        собранный += "\n## " + хвост.rstrip() + "\n"
    return собранный, len(фрагменты)


def changed_files(argv: Sequence[str]) -> list[str]:
    """Файлы изменения — списком из аргументов, а не запросом к площадке.

    Список отдаёт прогон (`git diff --name-only`), потому что ему он и так
    доступен: лишний запрос к API стоил бы квоты и добавил бы отказ там, где
    без него обходятся.
    """
    return [строка.strip() for строка in argv if строка.strip()]


def requires_entry(labels: Sequence[str]) -> bool:
    """Нужна ли запись этому PR — по типу изменения."""
    return bool(BEHAVIOUR_TYPES & set(labels))


def main(argv: Sequence[str] | None = None) -> int:
    парсер = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    парсер.add_argument(
        "--strict",
        action="store_true",
        help="язык записей — отказ, а не замечание (для релиза: публикация необратима)",
    )
    парсер.add_argument(
        "--require-entry",
        action="store_true",
        help="требовать хотя бы одну новую запись среди изменённых файлов",
    )
    парсер.add_argument(
        "--changed",
        nargs="*",
        default=[],
        help="изменённые файлы (вывод git diff --name-only)",
    )
    парсер.add_argument("--version", default="Не выпущено", help="заголовок раздела")
    парсер.add_argument(
        "--render", action="store_true", help="напечатать собранный раздел"
    )
    парсер.add_argument(
        "--fold",
        action="store_true",
        help=f"сложить фрагменты в {JOURNAL} и удалить их (подготовка версии)",
    )
    парсер.add_argument(
        "--section",
        action="store_true",
        help=f"напечатать раздел версии из {JOURNAL} (заметки выпуска)",
    )
    аргументы = парсер.parse_args(list(argv) if argv is not None else None)

    if аргументы.section:
        путь = ROOT / JOURNAL
        свод = путь.read_text(encoding="utf-8") if путь.is_file() else ""
        раздел = section(свод, аргументы.version)
        if раздел is None:
            print(
                f"::error::в {JOURNAL} нет раздела {аргументы.version}. "
                "Фрагменты складываются в свод при подготовке версии — иначе "
                "заметки собрались бы из записей прошлого выпуска",
                file=sys.stderr,
            )
            return EXIT_FAILED
        print(раздел)
        return 0

    фрагменты, претензии = collect(ROOT)
    замечания = language_warnings(фрагменты)

    if аргументы.require_entry:
        новые = [
            путь
            for путь in changed_files(аргументы.changed)
            if путь.startswith(f"{FRAGMENTS}/") and путь.endswith(".md")
        ]
        if not новые:
            претензии.append(
                f"изменение меняет поведение, а записи в {FRAGMENTS}/ нет. "
                "Запись, написанная после релиза, пишется по памяти и врёт — "
                "поэтому она едет вместе с изменением, а не следом"
            )

    if аргументы.render:
        print(render(фрагменты, аргументы.version))

    for замечание in замечания:
        print(f"::{'error' if аргументы.strict else 'warning'}::{замечание}")
    for претензия in претензии:
        print(f"::error::{претензия}")

    отказы = list(претензии) + (замечания if аргументы.strict else [])
    if отказы:
        print(
            f"\nзаписи не в порядке: {len(отказы)}. Формат — {FRAGMENTS}/README.md",
            file=sys.stderr,
        )
        return EXIT_FAILED

    if аргументы.fold:
        if not фрагменты:
            print(
                f"::error::складывать нечего: в {FRAGMENTS}/ нет записей. "
                "Выпуск без единой записи — повод остановиться и посмотреть, "
                "а не собрать пустой раздел",
                file=sys.stderr,
            )
            return EXIT_FAILED
        try:
            собранный, сложено = fold(ROOT, фрагменты, аргументы.version)
        except ValueError as exc:
            print(f"::error::{exc}", file=sys.stderr)
            return EXIT_FAILED
        (ROOT / JOURNAL).write_text(собранный, encoding="utf-8")
        for фрагмент in фрагменты:
            фрагмент.path.unlink()
        print(f"сложено в {JOURNAL}: раздел {аргументы.version}, записей {сложено}")
        return 0

    хвост = f", замечаний {len(замечания)}" if замечания else ""
    print(f"записей {len(фрагменты)}{хвост}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
