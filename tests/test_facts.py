"""Факты о проекте, публикуемые им самим (#103, правило 174 каталога).

Дороже всего здесь **число, которое выглядит точным и таковым не является**:
витрина соседа подпишет его именем издателя, и проверить его снаружи будет
нечем — ради этого файл и заводится. Поэтому почти все проверки ниже про
**отказ разделов**: удачный путь виден по любому прогону, а отказ не виден
никогда, пока его не подделать.

Раздел, посчитанный неточно, обязан ОТСУТСТВОВАТЬ, а не выйти нулём: ноль на
его месте читается как измеренный ответ («проверок не создаётся»), а не как
«не измеряли» (правило 039 — три исхода, а не два).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

import facts

КОРЕНЬ = Path(__file__).resolve().parents[1]
РЕПОЗИТОРИЙ = "ArtVsMark/Claude-Code_Usage-Token"


def _прогон(*, matrix: str = "", name: str = "гейты") -> str:
    """Подделочный workflow с одним джобом на pull_request."""
    return (
        "on:\n  pull_request:\n\njobs:\n"
        f"  gates:\n    name: {name}\n    runs-on: ubuntu-latest\n"
        + (f"    strategy:\n      matrix:\n{matrix}" if matrix else "")
        + "    steps:\n      - run: echo\n"
    )


# ── имена джобов ────────────────────────────────────────────────────────────


def test_джоб_без_матрицы_даёт_одно_имя() -> None:
    assert facts.имена_джобов(_прогон()) == ["гейты"]


def test_джоб_без_name_называется_идентификатором() -> None:
    """Так его называет площадка, и под этим именем проверка появится на PR."""
    текст = "on:\n  pull_request:\n\njobs:\n  verdict:\n    runs-on: ubuntu-latest\n"
    assert facts.имена_джобов(текст) == ["verdict"]


def test_матрица_разворачивается_в_каждую_ячейку() -> None:
    прогон = _прогон(
        matrix=(
            "        os: [ubuntu-latest, macos-latest]\n"
            '        python: ["3.12", "3.13"]\n'
        ),
        name="гейты · ${{ matrix.os }} · python ${{ matrix.python }}",
    )
    assert facts.имена_джобов(прогон) == [
        "гейты · ubuntu-latest · python 3.12",
        "гейты · ubuntu-latest · python 3.13",
        "гейты · macos-latest · python 3.12",
        "гейты · macos-latest · python 3.13",
    ]


def test_include_в_матрице_это_отказ() -> None:
    """`include` меняет состав ячеек — развёрнутое без него число было бы ложью."""
    прогон = _прогон(
        matrix=(
            "        os: [ubuntu-latest]\n"
            "        include:\n          - os: windows-latest\n"
        ),
        name="гейты · ${{ matrix.os }}",
    )
    assert facts.имена_джобов(прогон) is None


def test_чужая_подстановка_в_имени_это_отказ() -> None:
    """`github.*` здесь не развернуть, а имя вышло бы не тем, что на площадке."""
    прогон = _прогон(name="гейты · ${{ github.event_name }}")
    assert facts.имена_джобов(прогон) is None


# ── разделы файла ───────────────────────────────────────────────────────────


def test_проверки_считаются_вместе_с_агрегатором() -> None:
    """`PR check` виден на изменении наравне с остальными.

    `pr_check.expected_workflows` исключает себя по своей причине — агрегатор
    не ждёт сам себя, — но читателю фактов он такая же проверка.
    """
    итог = facts.проверки_на_изменении(КОРЕНЬ)
    assert итог is not None
    assert "PR check" in итог["names"]
    assert итог["count"] == len(итог["names"])


def test_имена_проверок_уникальны() -> None:
    """Считать полагается по уникальным именам: check-runs после обновления
    ветки удваиваются, и суммарное число врёт вдвое."""
    итог = facts.проверки_на_изменении(КОРЕНЬ)
    assert итог is not None
    assert len(set(итог["names"])) == итог["count"]


def test_раскладка_правил_сходится_с_общим_числом() -> None:
    """Иначе доли на витрине не сложатся, и заметит это уже читатель."""
    итог = facts.правила(КОРЕНЬ)
    assert итог is not None
    всего = итог.pop("total")
    assert sum(итог.values()) == всего


def test_питон_и_платформы_из_матрицы(tmp_path: Path) -> None:
    рабочие = tmp_path / ".github" / "workflows"
    рабочие.mkdir(parents=True)
    (tmp_path / facts.CI_WORKFLOW).write_text(
        _прогон(
            matrix='        os: [ubuntu-latest]\n        python: ["3.13"]\n',
            name="гейты · ${{ matrix.os }} · python ${{ matrix.python }}",
        ),
        encoding="utf-8",
    )
    assert facts.питон_и_платформы(tmp_path) == {
        "supported": ["3.13"],
        "os": ["ubuntu-latest"],
    }


def test_без_прогона_проверок_питон_не_публикуется(tmp_path: Path) -> None:
    """Ключа нет — «не измеряли». Пустой список читался бы как ответ."""
    assert facts.питон_и_платформы(tmp_path) is None


def test_без_набора_тестов_раздел_не_публикуется(tmp_path: Path) -> None:
    assert facts.тесты(tmp_path) is None


def test_испорченные_ответы_каталогу_не_дают_раскладки(tmp_path: Path) -> None:
    (tmp_path / ".rules").mkdir()
    (tmp_path / ".rules" / "bindings.json").write_text("не json", encoding="utf-8")
    assert facts.правила(tmp_path) is None


# ── файл целиком ────────────────────────────────────────────────────────────


def test_обязательный_минимум_на_месте() -> None:
    """Без этих трёх полей файл бесполезен: не прочесть, не понять о ком и когда."""
    ф = facts.build(КОРЕНЬ, repo=РЕПОЗИТОРИЙ)
    assert ф["schema"] == "1.0"
    assert isinstance(ф["schema"], str), "версия строкой: 1.0 и 1.10 иначе не различить"
    assert ф["repo"] == РЕПОЗИТОРИЙ
    assert datetime.fromisoformat(ф["generated_at"]).tzinfo is not None


def test_отметка_времени_с_поясом() -> None:
    отметка = datetime(2026, 9, 4, 12, 0, tzinfo=UTC)
    ф = facts.build(КОРЕНЬ, repo=РЕПОЗИТОРИЙ, now=отметка)
    assert ф["generated_at"] == "2026-09-04T12:00:00+00:00"


def test_без_имени_репозитория_отказ() -> None:
    """Из `git remote` оно не берётся: клон до переименования хранит старый
    адрес, git его не обновляет, и работает тот по редиректу площадки."""
    with pytest.raises(ValueError, match="GITHUB_REPOSITORY"):
        facts.build(КОРЕНЬ, repo="")


def test_пустое_дерево_даёт_минимум_и_ни_одного_раздела(tmp_path: Path) -> None:
    """Проекту, которому измерять нечего, не подсовывают нули.

    Файл при этом собирается: решение «не заводить его вовсе» принимает
    издатель, а не сборщик, — и здесь оно уже принято в пользу «заводить».
    """
    ф = facts.build(tmp_path, repo=РЕПОЗИТОРИЙ)
    assert set(ф) == {"schema", "schema_of", "repo", "generated_at"}


def test_версия_говорит_чего_она() -> None:
    """Ключ `schema` есть и у соседних файлов, а предметы у них разные."""
    assert facts.build(КОРЕНЬ, repo=РЕПОЗИТОРИЙ)["schema_of"] == "facts"


def test_main_пишет_файл_по_адресу_контракта(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    код = facts.main([str(tmp_path), "--repo", РЕПОЗИТОРИЙ])
    assert код == 0
    записано = json.loads((tmp_path / facts.FACTS_PATH).read_text(encoding="utf-8"))
    assert записано["repo"] == РЕПОЗИТОРИЙ


def test_main_без_имени_репозитория_не_пишет_ничего(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Отказ отделён от «нечего измерять»: код 2, и файла нет."""
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)
    assert facts.main([str(tmp_path)]) == facts.EXIT_BROKEN
    assert not (tmp_path / facts.FACTS_PATH).exists()


# ── тихая потеря раздела ────────────────────────────────────────────────────


def test_на_своём_дереве_измеряются_все_разделы() -> None:
    """Обещание `ИЗМЕРЯЕМ` — про это дерево, и здесь оно выполняется."""
    assert facts.не_измеренное(КОРЕНЬ) == []


def test_источника_нет_молчание_честно(tmp_path: Path) -> None:
    """Пустое дерево ничего не теряет: измерять там нечего."""
    assert facts.не_измеренное(tmp_path) == []


def test_источник_есть_а_числа_нет_это_потеря(tmp_path: Path) -> None:
    """Ровно тот случай, ради которого гейт заведён.

    Матрица с `include` разворачивается не так, как её разворачивает площадка,
    и раздел отказывается считаться. В `facts.json` его не будет, витрина
    соседа честно покажет «не измеряли» — и отличить это от «мы такого не
    меряем» будет нечем. Без гейта не покраснеет ничто.
    """
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / facts.CI_WORKFLOW).write_text(
        _прогон(
            matrix=(
                "        os: [ubuntu-latest]\n"
                "        include:\n          - os: windows-latest\n"
            ),
            name="гейты · ${{ matrix.os }}",
        ),
        encoding="utf-8",
    )
    потеряно = facts.не_измеренное(tmp_path)
    assert "checks_per_pr" in потеряно
    assert "python" in потеряно
    assert "tests" not in потеряно, "каталога тестов там нет — терять нечего"
