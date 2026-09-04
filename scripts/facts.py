"""Проект сам публикует факты о своём устройстве (#103, правило 174 каталога).

Витрина профиля показывает числа четырёх проектов рядом. Считать их снаружи —
значит держать копию чужого определения: где лежат тесты, как назван их
каталог, как устроена матрица. Копия верна до первой правки на той стороне и
расходится молча. Замер, из-за которого контракт появился: витрина брала у
соседа число проверок **медианой** по семи последним изменениям и показывала
19 там, где точный ответ издателя — 16.

## Здесь публикуется только точное

Контракт (`.rules/facts-contract.md` витрины) требует ровно этого: «файл
существует потому, что у издателя есть ТОЧНЫЙ ответ; медиана и „примерно
столько" из него не публикуются». Поэтому у каждого раздела ниже есть право
**отсутствовать**, и оно используется: разобрать матрицу не вышло — раздела
`python` в файле нет вовсе. Ключа нет — «не измеряли»; ноль на его месте
читался бы как измеренный ответ «проверок не создаётся» (правило 039).

Отсюда же вытекает, чего здесь НЕТ. Покрытие не публикуется: в проекте нет
инструмента замера, и число взялось бы из воздуха. Это отдельная работа, а не
пропущенная строка.

## Что означает `tests.functions`

Число **функций** `test_*` в `tests/`, а не прогоняемых случаев:
параметризация здесь не разворачивается. Ключ контракта называется
`functions`, и это ровно он. Развёрнутое число дал бы `pytest --collect-only`,
но за него пришлось бы платить установкой набора разработки в прогон значков —
а он обходится стандартной библиотекой, и это его заявленное свойство.

## Где файл живёт

На ветке `badges`, рядом со значками: собирается на каждый толчок в общую
ветку, то есть чаще, чем идут изменения (правило 160). Кладёт его туда тот же
прогон `.github/workflows/badges.yml`.
"""

from __future__ import annotations

import ast
import itertools
import json
import os
import re
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pr_check
import preflight
from utf8_output import force_utf8_output

#: Версия формата — СТРОКОЙ. Числовая не различает 1.0 и 1.10, и у соседей уже
#: разошлась: один проект публикует "1.0", другой 1 (контракт витрины, § версия).
SCHEMA = "1.0"

#: Ключ `schema` есть и у соседних файлов, а предметы у них разные — выгрузка
#: правил, ответ потребителя, предложение. Строка говорит, ЧЕГО эта версия
#: (правило 164 каталога).
SCHEMA_OF = "facts"

#: Куда кладётся файл на ветке значков. Путь назван контрактом витрины.
FACTS_PATH = ".github/badges/facts.json"

#: Прогон, чью матрицу читают разделы `python` и `checks_per_pr`.
CI_WORKFLOW = ".github/workflows/ci.yml"

#: Собрать не вышло: имя репозитория неизвестно, дерево не прочитано, диск отказал.
EXIT_BROKEN = 2

#: Разделы, которые этот проект обязуется измерять.
#:
#: Список не декоративный: отсутствие раздела в файле означает «не измеряли», и
#: означает это МОЛЧА. Матрица, в которую однажды допишут `include`, отняла бы
#: `checks_per_pr` без единого красного — а витрина соседа честно показала бы
#: «не измеряли» вместо числа, и разницу заметил бы только читатель. Держит
#: `scripts/preflight.py`: раздел из этого списка обязан считаться на дереве.
ИЗМЕРЯЕМ: tuple[str, ...] = ("tests", "python", "checks_per_pr", "rules")

_ДЖОБ = re.compile(r"^  ([A-Za-z_][\w-]*):[ \t]*$")
_ВЕРХНИЙ = re.compile(r"^\S")
_ИМЯ = re.compile(r"^    name:[ \t]*(.+?)[ \t]*$")
_MATRIX = re.compile(r"^      matrix:[ \t]*$")
_ОСЬ = re.compile(r"^        ([A-Za-z_][\w-]*):[ \t]*\[(.*)\][ \t]*$")
_ПОДСТАНОВКА = re.compile(r"\$\{\{[ \t]*matrix\.([A-Za-z_][\w-]*)[ \t]*\}\}")
_ОСТАТОК = re.compile(r"\$\{\{")


def свой_репозиторий() -> str:
    """Имя вида ``владелец/имя`` — из прогона, а не из ``git remote``.

    Клон, сделанный до переименования, хранит старый адрес: git его не
    обновляет, и работает тот по редиректу площадки. Живой источник один, и он
    есть только в прогоне (CLAUDE.md, § «Работа с GitHub»).
    """
    return os.environ.get("GITHUB_REPOSITORY", "").strip()


def _снять_кавычки(значение: str) -> str:
    значение = значение.strip()
    if len(значение) >= 2 and значение[0] == значение[-1] and значение[0] in "\"'":
        return значение[1:-1]
    return значение


def _джобы(текст: str) -> list[tuple[str, str]]:
    """Пары «идентификатор джоба — его блок» из раздела ``jobs:``."""
    начало = re.search(r"^jobs:[ \t]*$", текст, re.MULTILINE)
    if начало is None:
        return []

    строки = текст[начало.end() :].splitlines()
    джобы: list[tuple[str, str]] = []
    имя: str | None = None
    блок: list[str] = []
    for строка in строки:
        совпало = _ДЖОБ.match(строка)
        if совпало is not None:
            if имя is not None:
                джобы.append((имя, "\n".join(блок)))
            имя, блок = совпало.group(1), []
            continue
        if _ВЕРХНИЙ.match(строка):
            break
        блок.append(строка)
    if имя is not None:
        джобы.append((имя, "\n".join(блок)))
    return джобы


def матрица(блок: str) -> dict[str, list[str]] | None:
    """Оси матрицы джоба. ``None`` — разобрать точно не вышло.

    Отказ, а не пустая матрица: ``include``/``exclude`` меняют состав ячеек, и
    джоб с ними развернулся бы неверно — то есть дал бы число, которое выглядит
    точным и таковым не является.
    """
    внутри = False
    оси: dict[str, list[str]] = {}
    for строка in блок.splitlines():
        if _MATRIX.match(строка):
            внутри = True
            continue
        if not внутри:
            continue
        if строка.strip() and not строка.startswith("        "):
            break
        совпало = _ОСЬ.match(строка)
        if совпало is None:
            голая = re.match(r"^        ([A-Za-z_][\w-]*):[ \t]*$", строка)
            if голая is not None:
                return None  # include/exclude или список не в строку
            continue
        ключ, перечень = совпало.group(1), совпало.group(2)
        if ключ in ("include", "exclude"):
            return None
        оси[ключ] = [_снять_кавычки(x) for x in перечень.split(",") if x.strip()]
    return оси


def _подставить(шаблон: str, значения: dict[str, str]) -> str:
    """Развернуть `${{ matrix.X }}` значениями одной ячейки."""

    def заменить(совпало: re.Match[str]) -> str:
        return значения.get(совпало.group(1), совпало.group(0))

    return _ПОДСТАНОВКА.sub(заменить, шаблон)


def имена_джобов(текст: str) -> list[str] | None:
    """Имена check-run'ов, которые создаст этот прогон. ``None`` — точно не вышло.

    Имя джоба площадка составляет из ``name:`` с подставленными значениями
    матрицы, а без ``name:`` берёт идентификатор. Осталась подстановка, которую
    здесь не развернуть (``github.*``, ``env.*``, выражение), — отказ: имя
    вышло бы не тем, под которым проверка появится на изменении.
    """
    имена: list[str] = []
    for идентификатор, блок in _джобы(текст):
        шаблон = идентификатор
        for строка in блок.splitlines():
            совпало = _ИМЯ.match(строка)
            if совпало is not None:
                шаблон = _снять_кавычки(совпало.group(1))
                break

        оси = матрица(блок)
        if оси is None:
            return None
        if not оси:
            if _ОСТАТОК.search(шаблон):
                return None
            имена.append(шаблон)
            continue

        ключи = list(оси)
        for сочетание in itertools.product(*(оси[k] for k in ключи)):
            значения = dict(zip(ключи, сочетание, strict=True))
            развёрнуто = _подставить(шаблон, значения)
            if _ОСТАТОК.search(развёрнуто):
                return None
            имена.append(развёрнуто)
    return имена


def проверки_на_изменении(root: Path) -> dict[str, Any] | None:
    """Сколько check-run'ов создаётся на изменении и как они называются.

    Считается по прогонам, которые срабатывают на ``pull_request``, **включая**
    сам агрегатор `PR check`: его на изменении видно наравне с остальными, а
    `pr_check.expected_workflows` исключает себя по своей причине — агрегатор
    не ждёт сам себя.

    Имена возвращаются рядом с числом намеренно: читатель проверяет число, а не
    принимает его на веру (контракт витрины, § checks_per_pr).
    """
    пути = set(pr_check.expected_workflows(root)) | {pr_check.SELF_PATH}
    имена: list[str] = []
    for путь in sorted(пути):
        файл = root / путь
        if not файл.is_file():
            return None
        порция = имена_джобов(файл.read_text(encoding="utf-8"))
        if порция is None:
            return None
        имена.extend(порция)
    if not имена:
        return None
    return {"count": len(имена), "names": sorted(имена)}


def питон_и_платформы(root: Path) -> dict[str, list[str]] | None:
    """Версии Python и платформы — из матрицы прогона проверок.

    Берётся матрица, а не ``requires-python``: публикуется то, на чём проверки
    действительно идут. Объявленный интервал и запускаемая матрица — два разных
    утверждения, и путать их значит отвечать за чужое.
    """
    файл = root / CI_WORKFLOW
    if not файл.is_file():
        return None
    for _, блок in _джобы(файл.read_text(encoding="utf-8")):
        оси = матрица(блок)
        if оси and "python" in оси and "os" in оси:
            return {"supported": list(оси["python"]), "os": list(оси["os"])}
    return None


def тесты(root: Path) -> dict[str, int] | None:
    """Число функций ``test_*`` и модулей ``test_*.py`` в наборе.

    Считается разбором дерева, а не прогоном: развёрнутое параметризацией
    число — это случаи, а ключ контракта называется ``functions``.
    """
    модули = sorted((root / "tests").glob("test_*.py"))
    if not модули:
        return None
    функций = 0
    for модуль in модули:
        try:
            дерево = ast.parse(модуль.read_text(encoding="utf-8"))
        except (OSError, SyntaxError):
            return None
        функций += sum(
            1
            for узел in ast.walk(дерево)
            if isinstance(узел, ast.FunctionDef | ast.AsyncFunctionDef)
            and узел.name.startswith("test_")
        )
    return {"functions": функций, "modules": len(модули)}


def правила(root: Path) -> dict[str, int] | None:
    """Чем held каждое правило каталога — по ответу, который проект уже даёт.

    Раскладка сходится с общим числом: механизм называется у **действующих**
    правил, а неприменимые и отклонённые считаются отдельно — у них механизма
    нет по определению, и складывать их с «ничем» значило бы смешать «не наш
    случай» с «правило принято и ничем не держится».
    """
    файл = root / ".rules" / "bindings.json"
    if not файл.is_file():
        return None
    try:
        записи = json.loads(файл.read_text(encoding="utf-8"))["rules"]
    except (OSError, ValueError, KeyError):
        return None
    if not isinstance(записи, dict):
        return None

    итог: dict[str, int] = {"total": len(записи), "not_applicable": 0, "rejected": 0}
    for запись in записи.values():
        статус = запись.get("status")
        if статус == "not-applicable":
            итог["not_applicable"] += 1
            continue
        if статус == "rejected":
            итог["rejected"] += 1
            continue
        механизм = str(запись.get("mechanism") or "none")
        итог[механизм] = итог.get(механизм, 0) + 1
    return итог


@dataclass(frozen=True)
class Раздел:
    """Чем раздел считается и по чему видно, что считать его здесь есть из чего.

    Источник назван рядом со счётчиком, а не вторым списком: две классификации
    одной территории расходятся молча (правило 022).
    """

    считать: Callable[[Path], Any]
    источник: str


#: Раздел, его счётчик и источник. Один свод для сборки и для гейта.
РАЗДЕЛЫ: dict[str, Раздел] = {
    "tests": Раздел(тесты, "tests"),
    "python": Раздел(питон_и_платформы, CI_WORKFLOW),
    "checks_per_pr": Раздел(проверки_на_изменении, CI_WORKFLOW),
    "rules": Раздел(правила, ".rules/bindings.json"),
}


def не_измеренное(root: Path) -> list[str]:
    """Разделы, чей источник в дереве есть, а число из него не вышло.

    Разница существенная и в ней весь смысл гейта. Источника нет — измерять
    нечего, и молчание здесь честно. Источник **есть**, а раздел не посчитался —
    это и есть тихая потеря: в `facts.json` его не будет, витрина соседа честно
    покажет «не измеряли», и отличить это от «мы такого не меряем» будет нечем.
    """
    потеряно: list[str] = []
    for ключ in ИЗМЕРЯЕМ:
        раздел = РАЗДЕЛЫ[ключ]
        if not (root / раздел.источник).exists():
            continue
        if раздел.считать(root) is None:
            потеряно.append(ключ)
    return потеряно


def build(root: Path, *, repo: str, now: datetime | None = None) -> dict[str, Any]:
    """Собрать факты. Раздел, посчитанный неточно, в файл не попадает.

    Отметка времени обязательна: файл не исчезает, когда прогон издателя
    ломается, — он остаётся вчерашним, и снаружи это неотличимо от «числа не
    менялись».
    """
    if not repo:
        raise ValueError(
            "имя репозитория неизвестно: GITHUB_REPOSITORY не задан и --repo "
            "не передан. Из `git remote` оно не берётся — клон, сделанный до "
            "переименования, хранит старый адрес и работает по редиректу"
        )

    отметка = (now or datetime.now(UTC)).replace(microsecond=0)
    факты: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_of": SCHEMA_OF,
        "repo": repo,
        "generated_at": отметка.isoformat(),
    }
    for ключ, раздел in РАЗДЕЛЫ.items():
        значение = раздел.считать(root)
        if значение is not None:
            факты[ключ] = значение
    return факты


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8_output()

    аргументы = list(sys.argv[1:] if argv is None else argv)
    repo = свой_репозиторий()
    корень = preflight.ROOT
    if "--repo" in аргументы:
        repo = аргументы[аргументы.index("--repo") + 1]
        аргументы = [x for x in аргументы if x != "--repo" and x != repo]
    if аргументы:
        корень = Path(аргументы[0])

    try:
        факты = build(корень, repo=repo)
        путь = корень / FACTS_PATH
        путь.parent.mkdir(parents=True, exist_ok=True)
        путь.write_text(
            json.dumps(факты, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    except (OSError, ValueError, KeyError, TypeError) as отказ:
        print(f"::error::факты не собраны: {отказ}", file=sys.stderr)
        return EXIT_BROKEN

    разделы = [k for k in ИЗМЕРЯЕМ if k in факты]
    пропущено = [k for k in ИЗМЕРЯЕМ if k not in факты]
    print(f"собран {FACTS_PATH}: разделов {len(разделы)} — {', '.join(разделы)}")
    if пропущено:
        print(f"не измерено (ключа в файле нет): {', '.join(пропущено)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
