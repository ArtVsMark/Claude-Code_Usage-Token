"""Проба авто-мержа на подделанной площадке (#165, шаг 2).

Проба взводит настоящий PR, и взведённый PR площадка сольёт по зелёному.
Поэтому первым проверяется то, чего проба делать НЕ должна: взводить PR, у
которого обязательная проверка не красная, и оставлять взвод после себя.
"""

from typing import Any

import pytest

import automerge
import gh_rest
import merge_queue
import pr_check

ТЕЛО = "Тело.\n\n" + merge_queue.ПОДПИСЬ


class Площадка:
    """Подделка: PR, проверки на голове и взвод, который можно прочитать."""

    def __init__(self, *, цвет: str = "failure", переписать: bool = False) -> None:
        self.цвет = цвет
        self.переписать = переписать
        self.auto_merge: dict[str, Any] | None = None
        self.мутации: list[str] = []

    def request(self, метод: str, путь: str, **_: Any) -> Any:
        if путь.endswith("/check-runs"):
            if self.цвет == "нет":
                return {"check_runs": []}
            статус = "in_progress" if self.цвет == "ожидает" else "completed"
            return {
                "check_runs": [
                    {
                        "id": 1,
                        "name": pr_check.SELF_NAME,
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "id": 2,
                        "name": pr_check.SELF_NAME,
                        "status": статус,
                        "conclusion": None if статус != "completed" else self.цвет,
                    },
                ]
            }
        return {
            "number": 7,
            "title": "fix: проба",
            "state": "open",
            "node_id": "PR_7",
            "head": {"sha": "abc1234def"},
            "auto_merge": self.auto_merge,
        }

    def graphql(self, запрос: str, переменные: dict[str, Any]) -> dict[str, Any]:
        имя = "enable" if "enablePullRequestAutoMerge" in запрос else "disable"
        self.мутации.append(имя)
        if имя == "enable":
            тело = "площадка своё" if self.переписать else переменные["body"]
            self.auto_merge = {
                "merge_method": "squash",
                "commit_title": переменные["title"],
                "commit_message": тело,
            }
        else:
            self.auto_merge = None
        return {}


@pytest.fixture
def площадка(monkeypatch: pytest.MonkeyPatch) -> Площадка:
    поддельная = Площадка()
    monkeypatch.setattr(gh_rest, "request", поддельная.request)
    monkeypatch.setattr(gh_rest, "graphql", поддельная.graphql)
    monkeypatch.setattr(merge_queue, "squash_message", lambda *_: ТЕЛО)
    return поддельная


@pytest.mark.parametrize("цвет", ["success", "ожидает", "нет", "cancelled"])
def test_не_красный_pr_не_взводится(площадка: Площадка, цвет: str) -> None:
    """Иначе площадка сольёт его посреди пробы."""
    площадка.цвет = цвет
    assert automerge.probe("o/r", 7) == automerge.EXIT_BROKEN
    assert площадка.мутации == []


def test_принятый_взвод_прочитан_и_снят(площадка: Площадка) -> None:
    assert automerge.probe("o/r", 7) == automerge.EXIT_OK
    assert площадка.мутации == ["enable", "disable"]
    assert площадка.auto_merge is None


def test_переписанное_тело_это_находка(площадка: Площадка) -> None:
    """Тело несёт подпись: переписанное площадкой — довериться нельзя."""
    площадка.переписать = True
    assert automerge.probe("o/r", 7) == automerge.EXIT_FINDING
    assert площадка.мутации == ["enable", "disable"]


def test_взвод_снимается_даже_при_отказе_чтения(
    площадка: Площадка, monkeypatch: pytest.MonkeyPatch
) -> None:
    исходный = площадка.request

    def request(метод: str, путь: str, **kw: Any) -> Any:
        if путь.endswith("/pulls/7") and площадка.auto_merge is not None:
            raise gh_rest.GitHubError(метод, путь, 502, "мусор")
        return исходный(метод, путь, **kw)

    monkeypatch.setattr(gh_rest, "request", request)
    with pytest.raises(gh_rest.GitHubError):
        automerge.probe("o/r", 7)
    assert площадка.мутации == ["enable", "disable"]


def test_заголовок_в_форме_площадки() -> None:
    assert automerge.заголовок_уплотнения({"title": "fix: x", "number": 5}) == (
        "fix: x (#5)"
    )


def test_мутации_из_закрытого_списка() -> None:
    """Тексты, которые пошлёт проба, транспорт пропустит."""
    for запрос in (automerge.ВЗВЕСТИ, automerge.СНЯТЬ):
        совпало = gh_rest._ОПЕРАЦИЯ_RE.match(запрос)
        assert совпало and совпало.group("имя") in gh_rest.ГРАФQL_ОПЕРАЦИИ
