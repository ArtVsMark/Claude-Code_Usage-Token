"""Проверки будильника расписаний (#90).

Будильник имеет право заводить задачи, поэтому проверяется он на подделанных
ответах площадки и в первую очередь — на том, чего делать **не** должен:
плодить вторую задачу на тот же прогон, комментировать каждый повторный отказ,
трогать чужой pull request с совпавшим заголовком, будить на отмену.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import gh_rest
import schedule_alarm

WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"

ПРОГОН = "https://github.com/x/y/actions/runs/1"


class ФейковаяПлощадка:
    """Подделка REST: помнит, что у неё просили и что ей записали."""

    def __init__(self, issues: list[dict[str, Any]] | None = None) -> None:
        self.issues = issues or []
        self.записи: list[tuple[str, str, dict[str, Any] | None]] = []
        self.следующий = 100

    def request(
        self,
        method: str,
        path: str,
        *,
        body: dict[str, Any] | None = None,
        params: Any = None,
    ) -> Any:
        if method != "GET":
            self.записи.append((method, path, body))
        if method == "POST" and path.endswith("/issues"):
            self.следующий += 1
            return {"number": self.следующий}
        if method == "GET" and path.endswith("/issues"):
            return list(self.issues)
        return {}

    def paged(self, path: str, *, params: Any = None) -> list[Any]:
        ответ = self.request("GET", path, params=params)
        return ответ if isinstance(ответ, list) else []


@pytest.fixture
def площадка(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _установить(issues: list[dict[str, Any]] | None = None) -> ФейковаяПлощадка:
        двойник = ФейковаяПлощадка(issues)
        monkeypatch.setattr(gh_rest, "request", двойник.request)
        monkeypatch.setattr(gh_rest, "paged", двойник.paged)
        monkeypatch.setenv("GH_TOKEN", "подделка")
        return двойник

    return _установить


def _задача(номер: int, workflow: str, **поля: Any) -> dict[str, Any]:
    основа: dict[str, Any] = {
        "number": номер,
        "title": schedule_alarm.ЗАГОЛОВОК.format(workflow=workflow),
    }
    основа.update(поля)
    return основа


def _пути(двойник: ФейковаяПлощадка, метод: str) -> list[str]:
    return [путь for м, путь, _ in двойник.записи if м == метод]


# ── отказ заводит задачу, повторный — нет ─────────────────────────────────


def test_первый_отказ_заводит_задачу(площадка: Any) -> None:
    двойник = площадка()
    итог = schedule_alarm.поднять("x/y", "очередь мержей", ПРОГОН)
    assert "заведена задача #101" in итог
    assert _пути(двойник, "POST") == ["/repos/x/y/issues"]


def test_повторный_отказ_не_комментирует(площадка: Any) -> None:
    """48 комментариев в сутки — способ отписаться, а не адресат."""
    двойник = площадка([_задача(7, "очередь мержей")])
    итог = schedule_alarm.поднять("x/y", "очередь мержей", ПРОГОН)
    assert "#7" in итог and "без комментария" in итог
    assert _пути(двойник, "POST") == [], "повтор написал комментарий"
    assert _пути(двойник, "PATCH") == ["/repos/x/y/issues/7"]


def test_повторный_отказ_обновляет_ссылку(площадка: Any) -> None:
    двойник = площадка([_задача(7, "очередь мержей")])
    schedule_alarm.поднять("x/y", "очередь мержей", "https://новая/ссылка")
    _, _, тело = двойник.записи[0]
    assert тело is not None
    assert "https://новая/ссылка" in тело["body"]


# ── зелёное закрывает задачу ──────────────────────────────────────────────


def test_успех_закрывает_задачу(площадка: Any) -> None:
    двойник = площадка([_задача(7, "rules-inbox")])
    итог = schedule_alarm.снять("x/y", "rules-inbox", ПРОГОН)
    assert "#7" in итог and "закрыта" in итог
    assert _пути(двойник, "POST") == ["/repos/x/y/issues/7/comments"]
    закрытие = next(т for м, _, т in двойник.записи if м == "PATCH")
    assert закрытие == {"state": "closed", "state_reason": "completed"}


def test_успех_без_задачи_ничего_не_делает(площадка: Any) -> None:
    """«Нечего делать» обязано звучать: молчание читается как поломка."""
    двойник = площадка()
    итог = schedule_alarm.снять("x/y", "rules-inbox", ПРОГОН)
    assert "закрывать нечего" in итог
    assert двойник.записи == []


# ── чужое не трогаем ──────────────────────────────────────────────────────


def test_pull_request_с_тем_же_заголовком_не_считается(площадка: Any) -> None:
    """`/issues` отдаёт и PR: без отсева сторож правил бы чужое."""
    похожий = _задача(5, "очередь мержей", pull_request={"url": "…"})
    двойник = площадка([похожий])
    итог = schedule_alarm.поднять("x/y", "очередь мержей", ПРОГОН)
    assert "заведена задача" in итог
    assert _пути(двойник, "PATCH") == [], "правили pull request"


def test_задача_другого_прогона_не_считается(площадка: Any) -> None:
    двойник = площадка([_задача(7, "rules-inbox")])
    schedule_alarm.поднять("x/y", "очередь мержей", ПРОГОН)
    assert _пути(двойник, "POST") == ["/repos/x/y/issues"]


# ── исходы, на которые будить не надо ─────────────────────────────────────


@pytest.mark.parametrize("исход", ["cancelled", "skipped", "action_required", ""])
def test_ни_отказ_ни_успех_не_трогает_задач(
    площадка: Any, capsys: pytest.CaptureFixture[str], исход: str
) -> None:
    """Отмену делает человек или concurrency — будить на неё значит будить на себя."""
    двойник = площадка()
    код = schedule_alarm.main(
        ["--workflow", "очередь мержей", "--conclusion", исход, "--repo", "x/y"]
    )
    assert код == 0
    assert двойник.записи == []
    assert "не отказ и не успех" in capsys.readouterr().out


def test_таймаут_считается_падением(площадка: Any) -> None:
    двойник = площадка()
    код = schedule_alarm.main(
        ["--workflow", "rules-inbox", "--conclusion", "timed_out", "--repo", "x/y"]
    )
    assert код == 0
    assert _пути(двойник, "POST") == ["/repos/x/y/issues"]


# ── отказы механизма ──────────────────────────────────────────────────────


def test_без_токена_предупреждает_а_не_краснеет(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    код = schedule_alarm.main(
        ["--workflow", "rules-inbox", "--conclusion", "failure", "--repo", "x/y"]
    )
    assert код == 0
    assert "::warning::" in capsys.readouterr().err


def test_отказ_площадки_краснеет_отдельным_кодом(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Поломка механизма и «нечего делать» обязаны различаться кодом."""

    def падает(*args: Any, **kwargs: Any) -> Any:
        raise gh_rest.GitHubError("GET", "/issues", 403, "нет прав")

    monkeypatch.setenv("GH_TOKEN", "подделка")
    monkeypatch.setattr(gh_rest, "paged", падает)
    код = schedule_alarm.main(
        ["--workflow", "rules-inbox", "--conclusion", "failure", "--repo", "x/y"]
    )
    assert код == schedule_alarm.EXIT_BROKEN
    assert "::error::" in capsys.readouterr().err


# ── связь сторожа со сторожимыми ──────────────────────────────────────────


def _тело_блока(текст: str, ключ: str) -> list[str]:
    строки = текст.splitlines()
    начало = next(
        (н for н, с in enumerate(строки) if re.match(rf"^{ключ}\s*:", с)), None
    )
    if начало is None:
        return []
    тело = []
    for строка in строки[начало + 1 :]:
        if строка.strip() and not строка.startswith((" ", "\t")):
            break
        тело.append(строка)
    return тело


def _наблюдаемые(сторож: str) -> set[str]:
    """Имена из блока `workflows:` сторожа — и только из него.

    Соседний ключ `types:` тоже перечисляется дефисами, и брать «все элементы
    списка внутри `on:`» значило бы считать наблюдаемым прогон с именем
    `completed`. Ложно-зелёного из этого пока не выходило, но вышло бы при
    первом же совпадении имени прогона с именем события.
    """
    строки = сторож.splitlines()
    начало = next((н for н, с in enumerate(строки) if с.strip() == "workflows:"), None)
    if начало is None:
        return set()
    отступ = len(строки[начало]) - len(строки[начало].lstrip())
    имена: set[str] = set()
    for строка in строки[начало + 1 :]:
        if not строка.strip() or строка.strip().startswith("#"):
            continue
        if len(строка) - len(строка.lstrip()) <= отступ:
            break
        if строка.strip().startswith("- "):
            имена.add(строка.strip()[2:].strip())
    return имена


def test_каждый_прогон_по_расписанию_под_присмотром() -> None:
    """Переименовать прогон = тихо отключить сторожа. Держит этот тест.

    Площадка сопоставляет `workflows:` с `name:` дословно. Расхождение не
    краснеет нигде: сторож просто перестанет просыпаться, а красное по
    расписанию снова станет некому читать.
    """
    сторож = (WORKFLOWS / "schedule-alarm.yml").read_text(encoding="utf-8")
    наблюдаемые = _наблюдаемые(сторож)
    assert наблюдаемые, "список наблюдаемых пуст — тест проверял бы пустоту"

    по_расписанию: set[str] = set()
    for путь in sorted(WORKFLOWS.glob("*.yml")):
        текст = путь.read_text(encoding="utf-8")
        if not any(
            строка.strip().startswith("schedule:")
            for строка in _тело_блока(текст, "on")
        ):
            continue
        имя = re.search(r"^name:\s*(?P<имя>\S.*?)\s*$", текст, re.MULTILINE)
        assert имя is not None, f"{путь.name}: у прогона нет имени"
        по_расписанию.add(имя.group("имя"))

    assert по_расписанию, "прогонов по расписанию не нашлось — тест проверяет пустоту"
    забытые = по_расписанию - наблюдаемые
    assert not забытые, f"прогоны по расписанию без присмотра: {sorted(забытые)}"
