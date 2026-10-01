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
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

import facts

КОРЕНЬ = Path(__file__).resolve().parents[1]
РЕПОЗИТОРИЙ = "ArtVsMark/Claude-Code_Usage-Token"
КОММИТ = "0123456789abcdef0123456789abcdef01234567"

#: Настоящий `выпуск`, снятый до подмены: его проверяют отдельно, на настоящем git.
_ВЫПУСК = facts.выпуск


@pytest.fixture(autouse=True)
def _история_не_нужна(monkeypatch: pytest.MonkeyPatch) -> None:
    """Сборка целиком не должна зависеть от глубины клона, в котором идёт набор.

    `ci.yml` клонирует мелко, облачное окно тоже: настоящий `выпуск` там честно
    отказывает. Он проверяется своими тестами ниже, на собранном репозитории;
    остальным нужен только ответ — «выпусков нет».
    """
    monkeypatch.setattr(facts, "выпуск", lambda root: None)
    monkeypatch.setenv("GITHUB_SHA", КОММИТ)


def _git(где: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(где), *args], check=True, capture_output=True, timeout=30
    )


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
    """Минимум договора 1.2: без него витрина файл не примет вовсе."""
    ф = facts.build(КОРЕНЬ, repo=РЕПОЗИТОРИЙ)
    assert ф["schema"] == "1.2"
    assert isinstance(ф["schema"], str), "версия строкой: 1.0 и 1.10 иначе не различить"
    assert ф["repo"] == РЕПОЗИТОРИЙ
    assert datetime.fromisoformat(ф["generated_at"]).tzinfo is not None
    assert ф["commit"] == КОММИТ
    assert ф["ci"] == {"workflow": "ci.yml"}


def test_прогон_ci_существует() -> None:
    """Витрина спросит статус у площадки по этому имени: файла нет — нет статуса."""
    ф = facts.build(КОРЕНЬ, repo=РЕПОЗИТОРИЙ)
    assert (КОРЕНЬ / ".github" / "workflows" / ф["ci"]["workflow"]).is_file()


def test_по_каждому_показателю_значение_или_причина() -> None:
    """Правило договора одно, третьего исхода нет."""
    ф = facts.build(КОРЕНЬ, repo=РЕПОЗИТОРИЙ)
    for ключ in facts.ПОКАЗАТЕЛИ:
        assert (ключ in ф) != (ключ in ф.get("none", {})), ключ


def test_версия_та_же_что_у_значка() -> None:
    """Значок и факты называют одну версию одним числом."""
    import preflight

    ф = facts.build(КОРЕНЬ, repo=РЕПОЗИТОРИЙ)
    assert ф["version"] == preflight.project_version(КОРЕНЬ)


def test_короткий_коммит_это_отказ() -> None:
    with pytest.raises(ValueError, match="полный SHA"):
        facts.build(КОРЕНЬ, repo=РЕПОЗИТОРИЙ, commit="0123abc")


def test_причина_только_у_допустимых_ключей(tmp_path: Path) -> None:
    """Ключ `none` сверх перечня схема витрины отвергнет: `rules` туда не идёт."""
    ф = facts.build(tmp_path, repo=РЕПОЗИТОРИЙ)
    assert set(ф["none"]) <= set(facts.ПОКАЗАТЕЛИ)
    assert all(isinstance(v, str) and v for v in ф["none"].values())


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
    assert set(ф) == {
        "schema",
        "schema_of",
        "repo",
        "generated_at",
        "commit",
        "ci",
        "none",
    }
    assert set(ф["none"]) == set(facts.ПОКАЗАТЕЛИ), "нулей нет — есть причины"


def test_источник_есть_а_числа_нет_файл_не_собирается(tmp_path: Path) -> None:
    """«Не посчитали» в `none` выдало бы поломку за отсутствие предмета."""
    (tmp_path / ".github" / "workflows").mkdir(parents=True)
    (tmp_path / facts.CI_WORKFLOW).write_text(
        _прогон(matrix="        include:\n          - os: x\n"), encoding="utf-8"
    )
    with pytest.raises(ValueError, match="число не вышло"):
        facts.build(tmp_path, repo=РЕПОЗИТОРИЙ)


def test_версия_говорит_чего_она() -> None:
    """Ключ `schema` есть и у соседних файлов, а предметы у них разные: строка
    называет договор так, чтобы его можно было найти."""
    чего = facts.build(КОРЕНЬ, repo=РЕПОЗИТОРИЙ)["schema_of"]
    assert "ArtVsMark/ArtVsMark" in чего
    assert "facts.schema.json" in чего


# ── выпуск: на настоящем git ────────────────────────────────────────────────


@pytest.fixture
def репозиторий(tmp_path: Path) -> Path:
    """Полная история с одним коммитом и без тегов."""
    subprocess.run(
        ["git", "init", "-q", "-b", "main", str(tmp_path)],
        check=True,
        capture_output=True,
        timeout=30,
    )
    _git(tmp_path, "config", "user.email", "t@e.st")
    _git(tmp_path, "config", "user.name", "Тест")
    (tmp_path / "файл").write_text("x", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "начало")
    return tmp_path


def test_тег_выпуска_публикуется(репозиторий: Path) -> None:
    _git(репозиторий, "tag", "v0.2.0")
    assert _ВЫПУСК(репозиторий) == "v0.2.0"


def test_полная_история_без_тегов_это_выпусков_нет(репозиторий: Path) -> None:
    assert _ВЫПУСК(репозиторий) is None


def test_мелкий_клон_это_отказ_а_не_выпусков_нет(
    репозиторий: Path, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Ровно та ложь, что у витрины уже была: «не выпускался ни разу» (#59)."""
    _git(репозиторий, "tag", "v0.1.0")
    (репозиторий / "файл").write_text("y", encoding="utf-8")
    _git(репозиторий, "commit", "-q", "-am", "дальше")
    мелкий = tmp_path_factory.mktemp("мелкий")
    subprocess.run(
        [
            "git",
            "clone",
            "-q",
            "--depth",
            "1",
            "--no-tags",
            f"file://{репозиторий}",
            str(мелкий),
        ],
        check=True,
        capture_output=True,
        timeout=60,
    )
    with pytest.raises(ValueError, match="мелкий"):
        _ВЫПУСК(мелкий)


def test_не_git_это_отказ(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="не git"):
        _ВЫПУСК(tmp_path)


def test_main_пишет_файл_по_адресу_контракта(tmp_path: Path) -> None:
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_x.py").write_text(
        "def test_x() -> None: ...\n", encoding="utf-8"
    )
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
