"""Проверки гейта разметки pull request (#9).

У проверяющего инструмента две ошибки, и они несимметричны. Ложное «прошло» —
PR без метки уехал в `main`, задача осталась открытой, и трекер начал врать.
Ложное «не прошло» — завёрнут верный PR, человек правит работающую разметку;
такой гейт выключают первой же правкой вместе со всем, что он ловил.

Поэтому здесь на каждую находку по два теста: подделанный PR обязан находиться,
законный — проходить.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import check_pr_metadata as гейт

КОРЕНЬ = Path(__file__).resolve().parents[1]

#: Номенклатура живого документа. Тесты гоняются на ней, а не на выдуманной:
#: выдуманная разошлась бы с проектом молча — ровно то, от чего заведён разбор
#: `docs/labels.md` вместо констант.
НОМЕНКЛАТУРА = гейт.declared_labels(
    (КОРЕНЬ / гейт.LABELS_DOC).read_text(encoding="utf-8")
)


def _pr(
    *,
    labels: list[str] | None = None,
    body: str = "Closes #42",
    fork: bool = False,
    draft: bool = False,
    number: int = 1,
) -> dict[str, Any]:
    """Объект pull request в том виде, в каком его кладёт событие."""
    return {
        "number": number,
        "draft": draft,
        "body": body,
        # `labels or [...]` здесь был бы ошибкой: пустой список ложен, и
        # проверка «PR без меток» молча гонялась бы на PR с метками.
        "labels": [
            {"name": имя}
            for имя in (["area/ci", "enhancement"] if labels is None else labels)
        ],
        "head": {"repo": {"fork": fork}},
    }


def _проблемы(**kwargs: Any) -> list[str]:
    return гейт.metadata_problems(_pr(**kwargs), НОМЕНКЛАТУРА)


# ── номенклатура ──────────────────────────────────────────────────────────


def test_номенклатура_вычитывается_из_живого_документа() -> None:
    """Гейт читает состав меток из `docs/labels.md`, а не из своих констант.

    Тест на подделанном документе доказал бы, что разбор работает. Он не
    доказал бы, что разбирается **наш** документ: смена оформления таблицы
    оставила бы гейт с пустой номенклатурой, и он бы падал — но узнали бы об
    этом на первом же PR, а не здесь.
    """
    assert "area/ci" in НОМЕНКЛАТУРА.areas
    assert "area/storage" in НОМЕНКЛАТУРА.areas
    assert НОМЕНКЛАТУРА.types == {"bug", "enhancement", "documentation"}


def test_служебные_метки_не_стали_типами() -> None:
    """`wontfix` и `duplicate` живут в своей таблице и типом работы не являются.

    Разбор документа целиком склеил бы все таблицы, и `PR с меткой wontfix`
    прошёл бы как размеченный по типу.
    """
    assert not НОМЕНКЛАТУРА.types & {"wontfix", "duplicate", "epic", "question"}
    assert not НОМЕНКЛАТУРА.areas & {"difficulty/easy", "good first issue"}


def test_документ_без_таблиц_роняет_гейт() -> None:
    """Проверка, не нашедшая предмета, обязана упасть, а не выйти зелёной."""
    with pytest.raises(гейт.LabelsUnreadableError):
        гейт.declared_labels("# Метки\n\nТаблиц нет.\n")


# ── законное не заворачивается ────────────────────────────────────────────


def test_полная_разметка_проходит() -> None:
    assert _проблемы() == []


def test_fixes_считается_связью() -> None:
    """GitHub закрывает задачу по трём глаголам; требовать один — краснеть на верном."""
    assert _проблемы(body="Fixes #7") == []
    assert _проблемы(body="Resolves #7") == []
    assert _проблемы(body="closes #7") == []


def test_без_issue_с_причиной_проходит() -> None:
    assert _проблемы(body="Без issue: правка опечатки в комментарии") == []


def test_часть_задачи_считается_связью() -> None:
    """Форма нашлась на первом применении гейта — к его собственному PR.

    Задача #9 просит и гейт разметки, и метки конвейера, но метка заводится
    вместе с механизмом, которого ещё нет. Без этой формы оставалось два
    выхода, и оба плохие: `Closes` закрыл бы наполовину сделанную задачу, а
    `Без issue` соврал бы — задача есть.
    """
    assert _проблемы(body="Часть #9 — обязательные метки на PR") == []
    assert _проблемы(body="Часть #9 - обязательные метки на PR") == []


def test_часть_задачи_без_пояснения_не_связь() -> None:
    """«Часть #9» без слов о том, какая именно часть, — то же, что молчание."""
    проблемы = _проблемы(body="Часть #9")
    assert len(проблемы) == 1
    assert "Часть #N" in проблемы[0]


def test_незнакомая_форма_меток_не_роняет() -> None:
    """Реестр может вернуть мусор — гейт обязан отказать, а не сломаться."""
    pull: dict[str, Any] = {"number": 1, "labels": "area/ci", "body": "Closes #1"}
    проблемы = гейт.metadata_problems(pull, НОМЕНКЛАТУРА)
    assert len(проблемы) == 2  # нет зоны и нет типа
    assert гейт.pull_labels({"labels": [{"нет имени": 1}, "строка", None]}) == set()


# ── подделанное находится ─────────────────────────────────────────────────


def test_нет_зоны() -> None:
    проблемы = _проблемы(labels=["enhancement"])
    assert len(проблемы) == 1
    assert "нет метки area/*" in проблемы[0]


def test_зон_больше_одной() -> None:
    проблемы = _проблемы(labels=["area/ci", "area/docs", "enhancement"])
    assert len(проблемы) == 1
    assert "зон больше одной" in проблемы[0]


def test_выдуманная_зона() -> None:
    """Опечатка в зоне — не «зона есть».

    Проверка «хоть одна метка начинается с area/» пропустила бы `area/dosc`, и
    навигация по зонам тихо перестала бы работать для этого PR.
    """
    проблемы = _проблемы(labels=["area/dosc", "enhancement"])
    assert len(проблемы) == 1
    assert "нет в номенклатуре" in проблемы[0]
    assert "area/dosc" in проблемы[0]


def test_нет_типа() -> None:
    проблемы = _проблемы(labels=["area/ci"])
    assert len(проблемы) == 1
    assert "нет метки типа работы" in проблемы[0]


def test_типов_больше_одного() -> None:
    проблемы = _проблемы(labels=["area/ci", "bug", "documentation"])
    assert len(проблемы) == 1
    assert "типов больше одного" in проблемы[0]


def test_нет_связи_с_задачей() -> None:
    проблемы = _проблемы(body="Просто описание без ссылки на задачу.")
    assert len(проблемы) == 1
    assert "Часть #N" in проблемы[0]


def test_без_issue_с_пустой_причиной() -> None:
    """«Пустое поле причины равносильно отсутствию метки» — docs/labels.md."""
    проблемы = _проблемы(body="Без issue:")
    assert len(проблемы) == 1
    assert "Часть #N" in проблемы[0]

    проблемы = _проблемы(body="Без issue:    ")
    assert len(проблемы) == 1


def test_тело_отсутствует() -> None:
    """`body` бывает `null`, и это самый обычный случай — PR открыли пустым."""
    pull = _pr()
    pull["body"] = None
    assert len(гейт.metadata_problems(pull, НОМЕНКЛАТУРА)) == 1


# ── строгость по месту ────────────────────────────────────────────────────


def test_форк_предупреждение_а_не_отказ() -> None:
    вердикт = гейт.evaluate({"pull_request": _pr(labels=[], fork=True)}, НОМЕНКЛАТУРА)
    assert вердикт.ok
    assert len(вердикт.warnings) == 1
    assert "из форка" in вердикт.warnings[0]


def test_черновик_предупреждение_а_не_отказ() -> None:
    вердикт = гейт.evaluate({"pull_request": _pr(labels=[], draft=True)}, НОМЕНКЛАТУРА)
    assert вердикт.ok
    assert "черновик" in вердикт.warnings[0]


def test_готовый_pr_не_из_форка_отказ() -> None:
    вердикт = гейт.evaluate({"pull_request": _pr(labels=[], body="")}, НОМЕНКЛАТУРА)
    assert not вердикт.ok
    assert len(вердикт.findings) == 3  # зона, тип, связь
    assert all("PR #1" in находка for находка in вердикт.findings)


def test_размеченный_черновик_чист() -> None:
    """Черновик не должен получать предупреждение просто за то, что он черновик."""
    вердикт = гейт.evaluate({"pull_request": _pr(draft=True)}, НОМЕНКЛАТУРА)
    assert вердикт.ok
    assert вердикт.warnings == []


def test_событие_не_про_pr() -> None:
    вердикт = гейт.evaluate({"ref": "refs/heads/main"}, НОМЕНКЛАТУРА)
    assert вердикт.ok
    assert "не про pull request" in вердикт.warnings[0]


# ── прогон целиком ────────────────────────────────────────────────────────


def _событие(tmp_path: Path, pull: dict[str, Any] | None) -> str:
    файл = tmp_path / "event.json"
    полезное: dict[str, Any] = {} if pull is None else {"pull_request": pull}
    файл.write_text(json.dumps(полезное, ensure_ascii=False), encoding="utf-8")
    return str(файл)


def test_прогон_отказывает_ненулевым_кодом(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    код = гейт.main([_событие(tmp_path, _pr(labels=[], body=""))])

    assert код == гейт.EXIT_FAILED
    вывод = capsys.readouterr()
    assert "::error::" in вывод.out
    assert "разметки не хватает: 3" in вывод.err


def test_прогон_проходит_на_размеченном(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert гейт.main([_событие(tmp_path, _pr())]) == 0
    assert "у PR всё на месте" in capsys.readouterr().out


def test_прогон_без_пути_к_событию(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """«Проверку не удалось прогнать» — не то же, что «разметки не хватает»."""
    monkeypatch.delenv("GITHUB_EVENT_PATH", raising=False)

    assert гейт.main([]) == гейт.EXIT_BROKEN
    assert "не задан путь к событию" in capsys.readouterr().err


def test_прогон_на_мусоре_вместо_события(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    файл = tmp_path / "event.json"
    файл.write_text("{событие поехало", encoding="utf-8")

    assert гейт.main([str(файл)]) == гейт.EXIT_BROKEN
    assert "событие не прочитано" in capsys.readouterr().err


def test_прогон_на_событии_не_объекте(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    файл = tmp_path / "event.json"
    файл.write_text("[1, 2, 3]", encoding="utf-8")

    assert гейт.main([str(файл)]) == гейт.EXIT_BROKEN
    assert "событие не объект" in capsys.readouterr().err


def test_путь_берётся_из_окружения(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """В прогоне аргумента нет — путь приходит переменной, и это надо проверить."""
    monkeypatch.setenv("GITHUB_EVENT_PATH", _событие(tmp_path, _pr()))
    assert гейт.main([]) == 0
