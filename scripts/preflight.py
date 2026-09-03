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

import json
import re
import subprocess
import sys
import tomllib
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))

# Путь до соседних скриптов достроен строкой выше: приставка ветки живёт в
# гейте разметки, и второй раз её называть здесь нельзя — разойдётся молча.
import check_pr_metadata
import repo_links
import shell_ascii
import subprocess_encoding
import version
from utf8_output import force_utf8_output

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


# ── паритет витрин ────────────────────────────────────────────────────────

#: Русская витрина — источник истины, английская — перевод. Расхождение между
#: ними это **ошибка перевода, а не два разных утверждения** (CLAUDE.md).
SHOWCASE_RU = "README.md"
SHOWCASE_EN = "README.en.md"

_CYRILLIC = re.compile("[\u0400-\u04ff]")
_HEADING = re.compile(r"^(#{1,6}) ")

#: Ссылка-переключатель на другую витрину — единственное место, где название
#: чужого языка стоит на этом языке законно: «Русская версия» в английском
#: документе это не непереведённый кусок, а подпись к ссылке. Без этого
#: исключения проверка краснела бы на правильной строке — и её выключили бы
#: первой же правкой, вместе со всем остальным, что она ловит.
_LANG_SWITCH = re.compile(r"\[[^\]]*\]\(" + re.escape(SHOWCASE_RU) + r"\)")


def compare_showcases(root: Path) -> list[str]:
    """Сверить витрины и вернуть **предупреждения**, а не отказы.

    Расхождение витрин — состояние документации, а не дефект кода: оно не
    ломает поведение и не должно останавливать коммит. Но и молчать о нём
    нельзя, иначе заявление «витрина на двух языках» держится обещанием.

    Чего проверка **не** ловит: смысловое расхождение при формально верном
    переводе. Совпадение ключей — ещё не перевод; это остаётся за чтением.
    """
    ru_path, en_path = root / SHOWCASE_RU, root / SHOWCASE_EN
    if not (ru_path.is_file() and en_path.is_file()):
        return [f"витрин нет обеих: {SHOWCASE_RU} и {SHOWCASE_EN} — сверять нечего"]

    ru = ru_path.read_text(encoding="utf-8").splitlines()
    en = en_path.read_text(encoding="utf-8").splitlines()
    warnings: list[str] = []

    for number, line in enumerate(en, 1):
        if _CYRILLIC.search(_LANG_SWITCH.sub("", line)):
            warnings.append(
                f"{SHOWCASE_EN}:{number}: кириллица в английской витрине — "
                f"«{line.strip()[:60]}»"
            )

    ru_levels = [len(m.group(1)) for m in map(_HEADING.match, ru) if m]
    en_levels = [len(m.group(1)) for m in map(_HEADING.match, en) if m]
    if ru_levels != en_levels:
        warnings.append(
            f"структура заголовков разъехалась: в {SHOWCASE_RU} "
            f"{len(ru_levels)} ({ru_levels}), в {SHOWCASE_EN} "
            f"{len(en_levels)} ({en_levels})"
        )

    return warnings


# ── контракт витрины ──────────────────────────────────────────────────────
#
# Витрина отвечает на объявленный набор вопросов — или называет, чего нет.
#
# ПОЧЕМУ НАБОР ОДИН НА ВСЕ ПРОЕКТЫ. Он взят у каталога и не сокращён под свои
# возможности. Набор, подогнанный под то, на что проект уже отвечает, не с чем
# сравнить, и того, что проект перестал отвечать, никто не заметит: вопрос
# исчезнет вместе с ответом.
#
# ПОЧЕМУ ПРОБЕЛ НАЗЫВАЕТСЯ, А НЕ ОПУСКАЕТСЯ. Значок, которого нет, и значок,
# который застыл, с витрины НЕОТЛИЧИМЫ. «Покрытие не измеряется» и «механизм
# замера сломался и молчит» — разные вещи, и разница видна только тогда, когда
# пробел назван словом.
#
# ПОЧЕМУ ОТКАЗ, А НЕ ЗАМЕЧАНИЕ — в отличие от паритета витрин рядом. Граница
# проходит по достоверности: расхождение переводов проверка вычисляет по
# косвенным признакам и на законном тексте ошибается, поэтому предупреждает.
# Здесь ошибиться не на чем — вопрос либо имеет ровно один ответ, либо нет, и
# значок либо сходится с деревом, либо разошёлся. Достоверное запрещают.

#: Набор вопросов витрины. Живёт данными, а не константами в этом файле:
#: набор общий и приходит от каталога, а гейт свой. Жизненный цикл у них
#: разный, и держать их в одном месте значило бы править чужое при каждой
#: своей правке.
SHOWCASE_SET = ".rules/showcase.json"

#: Причина отсутствия короче этого — отписка, а не причина. Двадцать символов
#: не мера качества: это нижняя граница, ниже которой объяснения точно нет.
ABSENT_MIN = 20

#: Цвет значка постоянный. Цвет на shields.io читается как оценка, а оценивать
#: здесь нечего: значок сообщает число, а не своё мнение о нём.
BADGE_COLOR = "blue"

#: Набор обязан быть в витрине **ссылкой**, а не упоминанием.
#:
#: Разница нашлась попыткой провалить гейт, а не прогоном. Проверка сначала
#: искала в витрине саму строку пути — и оставалась зелёной, когда у ссылки
#: подменили адрес: путь никуда не делся, он остался в **подписи** ссылки
#: (``[`.rules/showcase.json`](…)``), и условие выполнялось на ней. То есть
#: гейт держал ровно тот случай, который не ломается, и пропускал тот, ради
#: которого заведён: с витрины до названных пробелов было уже не дойти.
_НАБОР_ССЫЛКОЙ = re.compile(
    r"\]\(<?" + re.escape(SHOWCASE_SET) + r"|\]:\s*<?" + re.escape(SHOWCASE_SET)
)


@dataclass(frozen=True)
class ShowcaseContract:
    """Находки и **счёт**: вопросов, живых ответов, названных пробелов.

    Счёт входит в вывод по той же причине, что и охват у проверки на секреты:
    «контракт витрины ✓» без чисел неотличимо от «набор пуст, и проверять было
    нечего». Считаются при этом **уникальные** вопросы, а не строки набора —
    повтор удваивал бы счёт, ничего не добавляя.
    """

    findings: list[str]
    questions: int
    live: int
    named: int


#: Строка объявления версии — та же форма, которую читает сборка.
_VERSION_RE = re.compile(r'^__version__\s*=\s*"([^"]+)"', re.MULTILINE)


def project_version(root: Path) -> str:
    """Версия проекта из единственного источника, названного в ``pyproject.toml``.

    Раньше бралась прямо из ``[project] version``. После #12 версия объявлена
    там динамической, и значок стало не с чем сверять — гейт заметил это тем же
    прогоном, что и всё остальное. Ровно та работа, ради которой он заведён.

    Путь к источнику **не задаётся здесь константой**: он читается из
    ``[tool.hatch.version] path``. Иначе одно и то же знание — «где живёт
    версия» — лежало бы в двух местах, и переезд источника разошёлся бы с
    гейтом молча.
    """
    with (root / "pyproject.toml").open("rb") as fh:
        путь = tomllib.load(fh)["tool"]["hatch"]["version"]["path"]
    if not isinstance(путь, str):
        raise TypeError(f"путь к версии в pyproject.toml не строка: {путь!r}")

    совпадение = _VERSION_RE.search((root / путь).read_text(encoding="utf-8"))
    if совпадение is None:
        raise ValueError(f'в {путь} нет строки __version__ = "…"')
    return совпадение.group(1)


def expected_badge(qid: str, root: Path) -> dict[str, object] | None:
    """Что обязано лежать в значке вопроса ``qid``, вычисленное из дерева.

    ``None`` означает «правила вывода для этого вопроса здесь нет». Это не
    разрешение пропустить: гейт, не нашедший предмета проверки, обязан
    сказать об этом, иначе объявленный значок сверяется с пустотой.
    """
    if qid == "version":
        return {
            "schemaVersion": 1,
            "label": "version",
            "message": project_version(root),
            "color": BADGE_COLOR,
        }
    return None


def _check_badge(
    qid: str,
    badge: str,
    question: dict[str, object],
    root: Path,
    shown: dict[str, str],
) -> list[str]:
    """Проверить объявленный значок: показан ли, есть ли он, сходится ли с деревом."""
    findings: list[str] = []

    имя = PurePosixPath(badge).name
    for витрина, текст in shown.items():
        if имя not in текст:
            findings.append(
                f"{qid}: значок {badge} объявлен, но в {витрина} его нет — "
                "ответ в пустоту"
            )

    if question.get("branch", "main") != "main":
        # Значок с отдельной ветки в этом дереве не лежит и лежать не должен:
        # ветка заводится ровно для того, чтобы пересборка значка не двигала
        # общую. Требовать файл здесь значило бы требовать того, чего быть не
        # может, — то есть заворачивать верное.
        return findings

    path = root / badge
    if not path.is_file():
        findings.append(
            f"{qid}: значок {badge} объявлен, а файла нет — витрина обещает "
            "ответ, которого не существует"
        )
        return findings

    try:
        expected = expected_badge(qid, root)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        findings.append(f"{qid}: значок {badge} не с чем сверить — {exc}")
        return findings

    if expected is None:
        findings.append(
            f"{qid}: значок {badge} объявлен, а правила вывода для него в "
            "гейте нет. Сверить его не с чем, и «зелено» здесь означало бы "
            "«не проверяли»"
        )
        return findings

    try:
        actual = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        findings.append(f"{qid}: значок {badge} не прочитан — {exc}")
        return findings

    if actual != expected:
        findings.append(
            f"{qid}: значок {badge} разошёлся с деревом — в файле {actual}, "
            f"из дерева выходит {expected}"
        )

    return findings


def check_showcase(root: Path) -> ShowcaseContract:
    """Сверить витрины с объявленным набором вопросов.

    Возвращает находки и счёт. Находка здесь — **отказ**: каждая из них
    решается однозначно, без догадок о намерении автора.
    """
    declared = root / SHOWCASE_SET
    try:
        questions = json.loads(declared.read_text(encoding="utf-8"))["questions"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return ShowcaseContract(
            [f"{SHOWCASE_SET}: набор вопросов витрины не прочитан — {exc}"], 0, 0, 0
        )

    if not isinstance(questions, list) or not questions:
        return ShowcaseContract(
            [
                f"{SHOWCASE_SET}: набор не называет ни одного вопроса — "
                "витрина без вопросов не витрина"
            ],
            0,
            0,
            0,
        )

    shown: dict[str, str] = {}
    for имя in (SHOWCASE_RU, SHOWCASE_EN):
        путь = root / имя
        if not путь.is_file():
            return ShowcaseContract(
                [f"{имя}: витрины нет — показывать ответы негде"], 0, 0, 0
            )
        shown[имя] = путь.read_text(encoding="utf-8")

    findings: list[str] = []

    for витрина, текст in shown.items():
        if _НАБОР_ССЫЛКОЙ.search(текст) is None:
            findings.append(
                f"{витрина}: на набор {SHOWCASE_SET} с витрины нет ссылки. "
                "Названный пробел, до которого нельзя дойти от витрины, "
                "назван только для того, кто и так знает, где смотреть"
            )

    live = 0
    named = 0
    seen: set[str] = set()

    for номер, question in enumerate(questions, 1):
        if not isinstance(question, dict):
            findings.append(f"вопрос №{номер}: запись не объект — разбирать нечего")
            continue

        qid = str(question.get("id", "")).strip()
        if not qid:
            findings.append(f"вопрос №{номер}: без id — на такой ответ не сослаться")
            continue

        if qid in seen:
            findings.append(
                f"{qid}: вопрос назван в наборе дважды. Считаются уникальные "
                "имена, а не строки: повтор удваивает счёт и прячет вопрос, "
                "которого в наборе нет"
            )
            continue
        seen.add(qid)

        badge = question.get("badge")
        absent = question.get("absent")

        if badge and absent:
            findings.append(f"{qid}: и значок, и причина отсутствия — ответ один")
            continue

        if not badge and not absent:
            findings.append(
                f"{qid} «{question.get('ask', '')}»: ответа нет вовсе. Либо "
                "значок, либо строка absent с причиной — пропуск и отсутствие "
                "предмета обязаны выглядеть по-разному"
            )
            continue

        if absent:
            named += 1
            if not isinstance(absent, str):
                # Отдельно от «коротка»: отказ обязан называть, что именно не
                # вышло, а «слишком коротка» про число — уже не название.
                findings.append(f"{qid}: причина отсутствия не строка — {absent!r}")
            elif len(absent.strip()) < ABSENT_MIN:
                findings.append(
                    f"{qid}: причина отсутствия слишком коротка, чтобы ею "
                    "что-то объяснить"
                )
            continue

        live += 1
        if not isinstance(badge, str):
            findings.append(f"{qid}: значок объявлен не путём — {badge!r}")
            continue

        findings.extend(_check_badge(qid, badge, question, root, shown))

    return ShowcaseContract(findings, len(seen), live, named)


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


def current_branch() -> str:
    """Имя текущей ветки — или пусто.

    Пусто означает открепленную голову, и в прогоне это норма: `checkout`
    ставит merge-коммит PR, у которого ветки нет вовсе. Поэтому пустое имя
    здесь не «гейт не нашёл предмета», а «проверять нечего по построению».
    """
    try:
        proc = subprocess.run(
            ["git", "branch", "--show-current"],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=False,
        )
    except OSError:
        return ""
    return proc.stdout.strip() if proc.returncode == 0 else ""


def branch_note(branch: str) -> str:
    """Замечание об имени ветки — или пусто, если сказать нечего.

    Замечание, а не отказ, и по месту: отказ живёт на pull request, где у него
    есть предмет спора и цена. Здесь задача другая — сказать до коммита, пока
    починка стоит одного `git branch -m`, а не перепушенной ветки и
    переоткрытого PR: head-ветку у открытого PR площадка менять не умеет.
    """
    приставка = check_pr_metadata.AGENT_BRANCH_PREFIX
    if not branch or branch == "main" or branch.startswith(приставка):
        return ""
    return (
        f"ветка `{branch}` не из `{приставка}**` — так называются ветки "
        f"агентского окна. Сейчас это `git branch -m {приставка}<имя>`, "
        "после открытия PR — перепушенная ветка и переоткрытый PR"
    )


def report(
    passed: Sequence[str],
    failed: Sequence[tuple[str, str]],
    warned: Sequence[str] = (),
) -> str:
    """Собрать итог так, чтобы он называл отказавшее по имени.

    Предупреждения печатаются знаком ``~`` и **не влияют на код возврата**:
    смешать их с отказами значило бы либо останавливать коммит из-за состояния
    документации, либо приучить пропускать красное.
    """
    lines = [f"  ✓ {name}" for name in passed]
    lines += [f"  ~ {name}" for name in warned]
    lines += [f"  ✗ {name}" for name, _ in failed]

    хвост = f" · замечаний {len(warned)}" if warned else ""
    if not failed:
        return "\n".join([*lines, "", f"всё чисто: проверок {len(passed)}{хвост}"])

    names = ", ".join(name for name, _ in failed)
    return "\n".join(
        [
            *lines,
            "",
            f"не прошло: {names}",
            f"(проверок всего {len(passed) + len(failed)}, "
            f"отказов {len(failed)}{хвост})",
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
    warned: list[str] = []

    for check in checks():
        ok, output = run_check(check)
        (passed.append(check.name) if ok else failed.append((check.name, output)))

    try:
        файлы = tracked_files()
        scan = scan_for_secrets(файлы)
    except (OSError, subprocess.CalledProcessError) as exc:
        # Отдельный код возврата: «проверку не удалось прогнать» — это не то же
        # самое, что «проверка нашла секрет», и путать их нельзя.
        print(
            f"preflight не отработал: не удалось перечислить файлы — {exc}",
            file=sys.stderr,
        )
        return EXIT_BROKEN

    заметка_о_ветке = branch_note(current_branch())
    if заметка_о_ветке:
        warned.append(f"имя ветки: {заметка_о_ветке}")

    показания = compare_showcases(ROOT)
    имя_витрин = "паритет витрин"
    if показания:
        warned.append(f"{имя_витрин}: расхождений {len(показания)}")
    else:
        passed.append(имя_витрин)

    имена_shell = shell_ascii.check_workflows(ROOT)
    имя_shell = (
        f"имена shell латиницей (workflow {len(shell_ascii.workflow_files(ROOT))})"
    )
    if имена_shell:
        failed.append((имя_shell, "\n".join(str(н) for н in имена_shell)))
    else:
        passed.append(имя_shell)

    кодировки = subprocess_encoding.check_tree(ROOT, files=файлы)
    имя_кодировок = f"кодировка подпроцессов названа (исходников {кодировки.examined})"
    if кодировки.находки:
        failed.append((имя_кодировок, "\n".join(str(н) for н in кодировки.находки)))
    else:
        passed.append(имя_кодировок)

    сведения = version.counted(ROOT)
    if сведения is None:
        # Третий исход, а не отказ (правило 039). Так клонирует облачное окно и
        # `actions/checkout` без `fetch-depth: 0`: тегов не видно, и версия
        # недостоверна — но дерево тут ни при чём, краснеть ему не за что.
        warned.append(
            "версия: тега схемы не видно, счёт недостоверен — git fetch --tags"
        )
    else:
        тег, полная, сколько = сведения
        имя_версии = f"версия ({полная}; с выпуска {тег} принято {сколько})"
        расхождение = version.check(ROOT)
        if расхождение:
            failed.append((имя_версии, "\n".join(расхождение)))
        else:
            passed.append(имя_версии)

    ссылки = repo_links.check_tree(ROOT, files=файлы)
    имя_ссылок = f"перепись ссылок (адресов в списке {len(repo_links.АДРЕСА)})"
    свежесть = repo_links.список_свежий(
        repo_links.АДРЕСА, из_прогона=repo_links.адрес_из_прогона()
    )
    if свежесть:
        failed.append((имя_ссылок, свежесть))
    elif ссылки:
        failed.append((имя_ссылок, "\n".join(str(н) for н in ссылки)))
    else:
        passed.append(имя_ссылок)

    контракт = check_showcase(ROOT)
    имя_контракта = (
        f"контракт витрины (вопросов {контракт.questions}, "
        f"живым числом {контракт.live}, названо без предмета {контракт.named})"
    )
    if контракт.findings:
        failed.append((имя_контракта, "\n".join(контракт.findings)))
    else:
        passed.append(имя_контракта)

    name = (
        f"секреты и замеры в диффе "
        f"(просмотрено {scan.examined}, пропущено двоичных {scan.skipped})"
    )
    if scan.findings:
        failed.append((name, "\n".join(scan.findings)))
    else:
        passed.append(name)

    if показания:
        print(
            f"── {имя_витрин} (замечания, на код возврата не влияют) ──",
            file=sys.stderr,
        )
        for показание in показания:
            print(f"  {показание}", file=sys.stderr)
        print(file=sys.stderr)

    for failed_name, output in failed:
        print(f"── {failed_name} ──", file=sys.stderr)
        if output:
            print(output, file=sys.stderr)
        print(file=sys.stderr)

    print(report(passed, failed, warned), file=sys.stderr)
    return EXIT_FAILED if failed else 0


if __name__ == "__main__":
    force_utf8_output()
    raise SystemExit(main(sys.argv[1:]))
