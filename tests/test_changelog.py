"""Проверки записей changelog (#10).

Ложное «прошло» здесь означает выпуск с недостоверным описанием возможностей —
то, что 📦 Релиз-инженер имеет право остановить. Ложное «не прошло» заворачивает
верную запись, и первой же правкой гейт выключают.

Поэтому на каждую находку — по два теста.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import changelog


def _фрагмент(каталог: Path, имя: str, текст: str) -> Path:
    каталог.mkdir(parents=True, exist_ok=True)
    путь = каталог / имя
    путь.write_text(текст, encoding="utf-8")
    return путь


def _дерево(tmp_path: Path, файлы: dict[str, str]) -> Path:
    каталог = tmp_path / changelog.FRAGMENTS
    for имя, текст in файлы.items():
        _фрагмент(каталог, имя, текст)
    return tmp_path


# ── формат имени ──────────────────────────────────────────────────────────


def test_запись_по_формату_разбирается(tmp_path: Path) -> None:
    путь = _фрагмент(tmp_path, "10.added.md", "Записи едут фрагментами.\n")
    разобрано = changelog.parse(путь)
    assert isinstance(разобрано, changelog.Fragment)
    assert (разобрано.issue, разобрано.kind) == (10, "added")


def test_имя_не_по_формату_находится(tmp_path: Path) -> None:
    путь = _фрагмент(tmp_path, "запись.md", "Текст.\n")
    assert isinstance(changelog.parse(путь), str)


def test_неизвестный_вид_находится(tmp_path: Path) -> None:
    """Список видов закрытый: неизвестный раздел потерялся бы при сборке молча."""
    путь = _фрагмент(tmp_path, "10.improved.md", "Текст.\n")
    претензия = changelog.parse(путь)
    assert isinstance(претензия, str)
    assert "неизвестен" in претензия


def test_пустая_запись_находится(tmp_path: Path) -> None:
    """Пустой файл — то же, что отсутствие записи, но выглядит как наличие."""
    путь = _фрагмент(tmp_path, "10.added.md", "   \n\n")
    претензия = changelog.parse(путь)
    assert isinstance(претензия, str)
    assert "пустая запись" in претензия


def test_readme_не_считается_записью(tmp_path: Path) -> None:
    """Описание формата лежит рядом с записями и записью не является."""
    корень = _дерево(
        tmp_path, {"README.md": "# Формат\n", "10.added.md": "Русская запись.\n"}
    )
    фрагменты, претензии = changelog.collect(корень)
    assert len(фрагменты) == 1
    assert претензии == []


def test_каталога_нет_собирать_не_из_чего(tmp_path: Path) -> None:
    """Проверка, не нашедшая предмета, обязана сказать об этом."""
    _, претензии = changelog.collect(tmp_path)
    assert len(претензии) == 1
    assert "каталога нет" in претензии[0]


# ── язык записей ──────────────────────────────────────────────────────────


def test_русская_запись_проходит(tmp_path: Path) -> None:
    корень = _дерево(tmp_path, {"10.added.md": "Записи едут фрагментами.\n"})
    фрагменты, _ = changelog.collect(корень)
    assert changelog.language_warnings(фрагменты) == []


def test_непереведённая_запись_находится(tmp_path: Path) -> None:
    корень = _дерево(tmp_path, {"10.added.md": "Entries now ship as fragments.\n"})
    фрагменты, _ = changelog.collect(корень)
    замечания = changelog.language_warnings(фрагменты)
    assert len(замечания) == 1
    assert "нет ни одной русской буквы" in замечания[0]


def test_запись_из_идентификаторов_законна(tmp_path: Path) -> None:
    """Ложное «не прошло», которое напрашивается само.

    «`ruff` поднят до 0.6» не содержит ни одной русской буквы по природе.
    Эвристика «нет кириллицы — не переведено» завернула бы верную запись, и
    гейт выключили бы вместе со всем, что он ловит. Поэтому исключение
    объявляется строкой-маркером, а не угадывается.
    """
    корень = _дерево(
        tmp_path,
        {"10.changed.md": f"{changelog.IDENTIFIERS_ONLY}\n`ruff` 0.6 → 0.16\n"},
    )
    фрагменты, _ = changelog.collect(корень)
    assert changelog.language_warnings(фрагменты) == []


def test_маркер_не_прячет_текст_целиком(tmp_path: Path) -> None:
    """Маркер снимает требование к языку, а не к содержанию."""
    корень = _дерево(
        tmp_path, {"10.changed.md": f"{changelog.IDENTIFIERS_ONLY}\n`ruff` 0.16\n"}
    )
    фрагменты, _ = changelog.collect(корень)
    assert фрагменты[0].body == "`ruff` 0.16"


# ── кому запись нужна ─────────────────────────────────────────────────────


def test_поведение_требует_записи() -> None:
    assert changelog.requires_entry(["area/ci", "enhancement"])
    assert changelog.requires_entry(["area/core", "bug"])


def test_правка_документов_записи_не_требует() -> None:
    """Требовать запись от правки документа значит разводить шум.

    В шуме теряется настоящая запись — та, ради которой гейт и заведён.
    """
    assert not changelog.requires_entry(["area/docs", "documentation"])


# ── сборка ────────────────────────────────────────────────────────────────


def test_сборка_разносит_по_разделам(tmp_path: Path) -> None:
    корень = _дерево(
        tmp_path,
        {
            "10.added.md": "Записи фрагментами.\n",
            "8.fixed.md": "Очередь не падает на конфликте.\n",
            "2.added.md": "Команда снятия замера.\n",
        },
    )
    фрагменты, _ = changelog.collect(корень)

    собрано = changelog.render(фрагменты, "0.1.0")

    assert "## 0.1.0" in собрано
    assert "### Добавлено" in собрано
    assert "### Исправлено" in собрано
    # Порядок по номеру задачи, а не по имени файла: строкой #10 встало бы
    # раньше #2.
    assert собрано.index("(#2)") < собрано.index("(#10)")


def test_пустой_раздел_не_печатается(tmp_path: Path) -> None:
    корень = _дерево(tmp_path, {"10.added.md": "Записи фрагментами.\n"})
    фрагменты, _ = changelog.collect(корень)

    собрано = changelog.render(фрагменты, "0.1.0")

    assert "### Убрано" not in собрано


def test_многострочная_запись_склеивается(tmp_path: Path) -> None:
    """В документе запись — один пункт списка, как бы её ни разбили в файле."""
    корень = _дерево(tmp_path, {"10.added.md": "Первая строка\nи вторая.\n"})
    фрагменты, _ = changelog.collect(корень)
    assert "- Первая строка и вторая. (#10)" in changelog.render(фрагменты, "0.1.0")


# ── строгость по месту ────────────────────────────────────────────────────


def test_на_pr_язык_замечание(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Запись ещё правится: красное здесь приучало бы читать красное как фон."""
    monkeypatch.setattr(
        changelog, "ROOT", _дерево(tmp_path, {"10.added.md": "In English.\n"})
    )

    assert changelog.main([]) == 0
    assert "::warning::" in capsys.readouterr().out


def test_при_релизе_язык_отказ(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Публикация необратима, поэтому здесь запрет, а не предупреждение."""
    monkeypatch.setattr(
        changelog, "ROOT", _дерево(tmp_path, {"10.added.md": "In English.\n"})
    )

    assert changelog.main(["--strict"]) == changelog.EXIT_FAILED
    assert "::error::" in capsys.readouterr().out


def test_отсутствие_записи_находится(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        changelog, "ROOT", _дерево(tmp_path, {"10.added.md": "Запись.\n"})
    )

    код = changelog.main(
        ["--require-entry", "--changed", "src/claude_code_usage/cli.py"]
    )

    assert код == changelog.EXIT_FAILED
    assert "записи в changelog.d/ нет" in capsys.readouterr().out


def test_новая_запись_в_диффе_считается(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        changelog, "ROOT", _дерево(tmp_path, {"10.added.md": "Запись.\n"})
    )

    код = changelog.main(
        ["--require-entry", "--changed", "src/x.py", "changelog.d/10.added.md"]
    )

    assert код == 0


def test_записи_проекта_в_порядке() -> None:
    """Гейт прогоняется на самом репозитории, а не только на подделках.

    Числа записей здесь нет намеренно. Прежняя редакция требовала «хотя бы
    одну» — и это перестало быть правдой ровно тогда, когда фрагменты начали
    расходоваться складыванием (#49): сразу после подготовки версии каталог
    пуст, и это законное состояние, а не поломка.
    """
    фрагменты, претензии = changelog.collect(changelog.ROOT)

    assert претензии == []
    assert changelog.language_warnings(фрагменты) == []


# ── свод: фрагменты расходуются складыванием (#49) ────────────────────────


def _дерево_свода(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **записи: str
) -> Path:
    """Отдельное дерево с фрагментами и подменённым корнем."""
    каталог = tmp_path / changelog.FRAGMENTS
    каталог.mkdir(parents=True)
    for имя, текст in записи.items():
        (каталог / имя.replace("__", ".")).write_text(текст, encoding="utf-8")
    monkeypatch.setattr(changelog, "ROOT", tmp_path)
    return tmp_path


def test_складывание_создаёт_свод_и_расходует_фрагменты(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Главное требование #49: после складывания фрагментов не остаётся.

    Пока они оставались на месте, заметки следующего выпуска повторили бы
    записи прошлого целиком — и увидели бы это уже на странице выпуска.
    """
    корень = _дерево_свода(
        tmp_path, monkeypatch, **{"7__added__md": "Первая запись.\n"}
    )

    код = changelog.main(["--fold", "--version", "v0.1.0"])

    свод = (корень / changelog.JOURNAL).read_text(encoding="utf-8")
    assert код == 0
    assert "## v0.1.0" in свод
    assert "Первая запись." in свод
    assert list((корень / changelog.FRAGMENTS).glob("*.md")) == []


def test_второе_складывание_той_же_версии_отказ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Иначе раздел удвоился бы, и заметки соврали бы дважды."""
    корень = _дерево_свода(tmp_path, monkeypatch, **{"7__added__md": "Запись.\n"})
    changelog.main(["--fold", "--version", "v0.1.0"])
    (корень / changelog.FRAGMENTS / "8.fixed.md").write_text("Другая.\n", "utf-8")

    assert changelog.main(["--fold", "--version", "v0.1.0"]) == changelog.EXIT_FAILED


def test_складывать_нечего_это_отказ(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Выпуск без единой записи — повод остановиться, а не собрать пустое."""
    _дерево_свода(tmp_path, monkeypatch)

    assert changelog.main(["--fold", "--version", "v0.1.0"]) == changelog.EXIT_FAILED


def test_новый_раздел_ложится_сверху(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Свежее читают первым; старые разделы остаются нетронутыми."""
    корень = _дерево_свода(tmp_path, monkeypatch, **{"7__added__md": "Первая.\n"})
    changelog.main(["--fold", "--version", "v0.1.0"])
    (корень / changelog.FRAGMENTS / "8.fixed.md").write_text("Вторая.\n", "utf-8")
    changelog.main(["--fold", "--version", "v0.2.0"])

    свод = (корень / changelog.JOURNAL).read_text(encoding="utf-8")

    assert свод.index("## v0.2.0") < свод.index("## v0.1.0")
    assert "Первая." in свод and "Вторая." in свод


def test_раздел_версии_достаётся_целиком() -> None:
    свод = (
        "# Ж\n\n## v0.2.0\n\n### Исправлено\n\n- Вторая. (#8)\n"
        "\n## v0.1.0\n\n- Первая. (#7)\n"
    )

    раздел = changelog.section(свод, "v0.2.0")

    assert раздел is not None
    assert "Вторая." in раздел
    assert "Первая." not in раздел, "в раздел попали чужие записи"


def test_раздела_нет_это_none() -> None:
    assert changelog.section("# Ж\n\n## v0.1.0\n\n- Первая.\n", "v0.9.9") is None


def test_последний_раздел_не_обрезается() -> None:
    """Границей последнего раздела служит конец файла, а не следующий «##»."""
    раздел = changelog.section("# Ж\n\n## v0.1.0\n\n- Первая. (#7)\n", "v0.1.0")

    assert раздел is not None and "Первая." in раздел


def test_заметки_выпуска_отказывают_без_раздела(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Раздела нет — значит фрагменты не сложены.

    Собрать заметки «как-нибудь» здесь хуже отказа: публикация необратима, и
    в выпуск уехали бы записи прошлого.
    """
    _дерево_свода(tmp_path, monkeypatch, **{"7__added__md": "Запись.\n"})

    assert changelog.main(["--section", "--version", "v0.1.0"]) == changelog.EXIT_FAILED


def test_язык_проверяется_на_складывании(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ переехал сюда из прогона выпуска — туда, где запись ещё правят."""
    _дерево_свода(tmp_path, monkeypatch, **{"7__added__md": "Bumped ruff to 0.6.\n"})

    assert (
        changelog.main(["--fold", "--strict", "--version", "v0.1.0"])
        == changelog.EXIT_FAILED
    )


#: Шаг workflow, который собирает список изменённых файлов. Проверяется формой,
#: а не прогоном: логика живёт в YAML, и запустить её тут нечем.
_WORKFLOW = Path(__file__).resolve().parents[1] / ".github/workflows/changelog.yml"


def test_наличие_обоих_концов_проверяется_а_не_предполагается() -> None:
    """`fetch-depth: 0` не гарантирует, что оба коммита в клоне.

    Замер 2026-09-02: на событии `edited` — которое файлов не меняет вовсе —
    шаг дважды подряд упал с «Invalid symmetric difference expression», хотя
    двумя прогонами раньше на той же голове был зелёным. Прежняя редакция
    комментария прямо утверждала «прогон уже держит оба коммита»; утверждение
    оказалось неверным.

    Тест держит проверку от «упрощения» обратно: соблазн вернуть одну строку с
    `git diff` возвращается при каждом чтении файла.
    """
    текст = _WORKFLOW.read_text(encoding="utf-8")

    assert 'git cat-file -e "${sha}^{commit}"' in текст, (
        "наличие концов обязано проверяться явно"
    )
    assert "refs/remotes/origin/*" in текст, "недостающее обязано дотягиваться"


def test_недостающий_коммит_роняет_шаг_а_не_даёт_пустой_список() -> None:
    """Пустой список изменённых файлов — это «PR ничего не менял».

    Гейт на нём отвечает «записи не требуется» и пропускает PR без записи. То
    есть отказ, превращённый в пустоту, выключил бы проверку молча — ровно то,
    от чего заведён `shell_ascii.py` этажом раньше.
    """
    текст = _WORKFLOW.read_text(encoding="utf-8")

    assert "не дотянулся" in текст and "exit 1" in текст, (
        "недостающий коммит обязан быть отказом"
    )
    assert "::error::" in текст, "отказ обязан называть коммит поимённо"
