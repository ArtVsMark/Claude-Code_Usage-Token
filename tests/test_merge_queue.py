"""Проверки обхода очереди (#8).

Обход имеет право мержить в `main`, поэтому проверяется он на подделанных
ответах площадки и в первую очередь — на том, чего делать **не** должен:
мержить дважды за прогон, останавливаться на конфликтном PR, ставить метку
второй раз.
"""

from __future__ import annotations

from typing import Any

import pytest

import gh_rest
import merge_queue
import pr_ready

ЭТАЛОН = frozenset({"гейты"})


def _pull(номер: int, **поля: Any) -> dict[str, Any]:
    основа: dict[str, Any] = {
        "number": номер,
        "state": "open",
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "body": "Closes #1",
        "labels": [{"name": "area/ci"}, {"name": "enhancement"}],
        "head": {"sha": f"sha{номер}", "repo": {"fork": False}},
        "base": {"ref": "main", "sha": "base-sha"},
    }
    основа.update(поля)
    return основа


def _снимок(pull: dict[str, Any]) -> pr_ready.Snapshot:
    return pr_ready.Snapshot(
        pull=pull,
        checks=[{"name": "гейты", "status": "completed", "conclusion": "success"}],
        expected=ЭТАЛОН,
    )


# ── метки приводятся в соответствие вердикту ──────────────────────────────


def test_конфликтный_pr_метится() -> None:
    """Конфликт метится `needs-rebase`, но признак «уедет по зелёному» остаётся.

    Метки отвечают на разные вопросы: `merge-when-green` — «умолчание мержить»,
    и оно не отменяется конфликтом; `needs-rebase` — «сейчас не едет, и вот
    почему». Снимать первую при конфликте значило бы выражать состоянием
    решение, которого никто не принимал.
    """
    pull = _pull(1, mergeable_state="dirty")
    вердикт = pr_ready.evaluate(_снимок(pull))

    сделано = merge_queue.reconcile_labels("o/r", _снимок(pull), вердикт, dry=True)

    assert f"поставить {pr_ready.NEEDS_REBASE}" in сделано
    assert f"снять {pr_ready.MERGE_WHEN_GREEN}" not in сделано


def test_ушедший_конфликт_снимает_метку() -> None:
    pull = _pull(
        1,
        labels=[
            {"name": "area/ci"},
            {"name": "enhancement"},
            {"name": pr_ready.NEEDS_REBASE},
        ],
    )
    вердикт = pr_ready.evaluate(_снимок(pull))

    сделано = merge_queue.reconcile_labels("o/r", _снимок(pull), вердикт, dry=True)

    assert f"снять {pr_ready.NEEDS_REBASE}" in сделано


def test_умолчание_мержить_ставит_видимый_признак() -> None:
    вердикт = pr_ready.evaluate(_снимок(_pull(1)))
    сделано = merge_queue.reconcile_labels("o/r", _снимок(_pull(1)), вердикт, dry=True)
    assert f"поставить {pr_ready.MERGE_WHEN_GREEN}" in сделано


def test_hold_снимает_признак() -> None:
    """Стоп-метка выключает видимый признак, а не наоборот."""
    pull = _pull(
        1,
        labels=[
            {"name": "area/ci"},
            {"name": "enhancement"},
            {"name": pr_ready.HOLD},
            {"name": pr_ready.MERGE_WHEN_GREEN},
        ],
    )
    вердикт = pr_ready.evaluate(_снимок(pull))

    сделано = merge_queue.reconcile_labels("o/r", _снимок(pull), вердикт, dry=True)

    assert f"снять {pr_ready.MERGE_WHEN_GREEN}" in сделано


def test_повторный_обход_ничего_не_меняет() -> None:
    """Идемпотентность: обход ходит по расписанию и не должен дёргать метки."""
    pull = _pull(
        1,
        labels=[
            {"name": "area/ci"},
            {"name": "enhancement"},
            {"name": pr_ready.MERGE_WHEN_GREEN},
        ],
    )
    вердикт = pr_ready.evaluate(_снимок(pull))

    assert merge_queue.reconcile_labels("o/r", _снимок(pull), вердикт, dry=True) == []


# ── обход целиком ─────────────────────────────────────────────────────────


class ФейковаяПлощадка:
    """Подделанные ответы REST и журнал изменяющих запросов."""

    def __init__(
        self,
        pulls: list[dict[str, Any]],
        *,
        main_busy: bool = False,
        main_red: bool = False,
        behind_by: int = 0,
    ) -> None:
        self.pulls = {p["number"]: p for p in pulls}
        self.main_busy = main_busy
        self.main_red = main_red
        self.behind_by = behind_by
        self.записи: list[tuple[str, str]] = []
        self.сравнения: list[str] = []

    def request(
        self, method: str, path: str, *, body: Any = None, params: Any = None
    ) -> Any:
        if method != "GET":
            self.записи.append((method, path))
            return {}
        if path.endswith("/actions/runs"):
            прогоны = (
                [
                    {
                        "status": "in_progress" if self.main_busy else "completed",
                        "conclusion": None if self.main_busy else "success",
                        "head_sha": "main-sha",
                    }
                ]
                if self.main_busy
                else []
            )
            прогоны.append(
                {
                    "status": "completed",
                    "conclusion": "failure" if self.main_red else "success",
                    "head_sha": "main-sha",
                }
            )
            return {"workflow_runs": прогоны}
        if "/compare/" in path:
            self.сравнения.append(path)
            return {"behind_by": self.behind_by, "ahead_by": 1}
        if path.endswith("/check-runs"):
            return {
                "check_runs": [
                    {"name": "гейты", "status": "completed", "conclusion": "success"}
                ]
            }
        if "/pulls/" in path:
            номер = int(path.rsplit("/", 1)[1])
            return self.pulls[номер]
        if path.endswith("/pulls"):
            return list(self.pulls.values())
        raise AssertionError(f"неожиданный путь: {path}")

    def paged(self, path: str, *, params: Any = None) -> list[Any]:
        ответ = self.request("GET", path, params=params)
        return ответ if isinstance(ответ, list) else []


@pytest.fixture
def площадка(monkeypatch: pytest.MonkeyPatch) -> Any:
    def _установить(**kwargs: Any) -> ФейковаяПлощадка:
        двойник = ФейковаяПлощадка(**kwargs)
        # Патчится один модуль: `merge_queue` держит ссылку на тот же объект
        # в sys.modules, и второй setattr был бы не страховкой, а видимостью
        # страховки.
        monkeypatch.setattr(gh_rest, "request", двойник.request)
        monkeypatch.setattr(gh_rest, "paged", двойник.paged)
        return двойник

    return _установить


def _мержи(двойник: ФейковаяПлощадка) -> list[str]:
    return [путь for метод, путь in двойник.записи if путь.endswith("/merge")]


def test_за_прогон_мержится_не_больше_одного(площадка: Any) -> None:
    """Мерж внахлёст вытесняет ожидающий прогон, и тот не начинается вовсе.

    Замер в соседнем проекте: шесть мержей подряд дали шесть отменённых
    прогонов и ни одного выполненного — шесть состояний `main` уехали без
    единой проверки.
    """
    двойник = площадка(pulls=[_pull(1), _pull(2), _pull(3)])

    merge_queue.run("o/r", "main", dry=False)

    assert _мержи(двойник) == ["/repos/o/r/pulls/1/merge"]


def test_конфликтный_pr_не_держит_очередь(
    площадка: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Инцидент соседа, учтённый заранее: конфликт в голове ронял обход.

    Три падения подряд, очередь стояла 14 часов, рядом ждали четыре здоровых
    PR. Конфликт — штатная ситуация: пометить и идти дальше.
    """
    двойник = площадка(pulls=[_pull(1, mergeable_state="dirty"), _pull(2)])

    merge_queue.run("o/r", "main", dry=False)

    assert _мержи(двойник) == ["/repos/o/r/pulls/2/merge"]
    assert "/labels" in " ".join(путь for _, путь in двойник.записи)
    assert "конфликт" in capsys.readouterr().out


def test_отставшая_голова_обновляется_а_не_мержится(площадка: Any) -> None:
    двойник = площадка(pulls=[_pull(1, mergeable_state="behind"), _pull(2)])

    merge_queue.run("o/r", "main", dry=False)

    пути = [путь for _, путь in двойник.записи]
    assert "/repos/o/r/pulls/1/update-branch" in пути
    assert _мержи(двойник) == []


def test_красный_main_останавливает_очередь(площадка: Any) -> None:
    двойник = площадка(pulls=[_pull(1)], main_red=True)

    merge_queue.run("o/r", "main", dry=False)

    assert _мержи(двойник) == []


def test_идущий_прогон_на_main_останавливает_очередь(площадка: Any) -> None:
    двойник = площадка(pulls=[_pull(1)], main_busy=True)

    merge_queue.run("o/r", "main", dry=False)

    assert _мержи(двойник) == []


def test_сухой_прогон_ничего_не_меняет(площадка: Any) -> None:
    """Кнопка «показать решение» обязана быть без последствий."""
    двойник = площадка(pulls=[_pull(1), _pull(2)])

    merge_queue.run("o/r", "main", dry=True)

    assert двойник.записи == []


def test_пустая_очередь_не_ходит_за_состоянием(
    площадка: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    двойник = площадка(pulls=[])

    assert merge_queue.run("o/r", "main", dry=False) == 0
    assert "очередь пуста" in capsys.readouterr().out
    assert двойник.записи == []


# ── заведение меток ───────────────────────────────────────────────────────


def test_существующая_метка_не_ошибка(monkeypatch: pytest.MonkeyPatch) -> None:
    """422 означает «уже есть» — это результат, а не отказ."""

    def request(method: str, path: str, **kwargs: Any) -> Any:
        raise gh_rest.GitHubError(method, path, 422, "already_exists")

    monkeypatch.setattr(gh_rest, "request", request)
    merge_queue.ensure_labels("o/r")  # не должно поднять исключение


def test_отказ_в_правах_не_проглатывается(monkeypatch: pytest.MonkeyPatch) -> None:
    """403 — это отсутствие прав, и молчать о нём нельзя."""

    def request(method: str, path: str, **kwargs: Any) -> Any:
        raise gh_rest.GitHubError(method, path, 403, "forbidden")

    monkeypatch.setattr(gh_rest, "request", request)
    with pytest.raises(gh_rest.GitHubError):
        merge_queue.ensure_labels("o/r")


def test_без_токена_прогон_предупреждает_а_не_падает(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Красное здесь означало бы поломку механизма, а не ненастроенное удобство."""
    monkeypatch.delenv("GH_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)

    assert merge_queue.main([]) == 0
    assert "::warning::" in capsys.readouterr().out


def test_отставание_считается_сравнением_с_веткой(площадка: Any) -> None:
    """Сравнивается имя ветки, а не `base.sha`.

    `base.sha` — состояние базы на момент открытия PR, и сравнение с ним всегда
    дало бы ноль: PR по определению основан на нём. Отставание считается от
    того, где общая ветка сейчас.
    """
    двойник = площадка(pulls=[_pull(1)], behind_by=2)

    merge_queue.run("o/r", "main", dry=True)

    assert двойник.сравнения == ["/repos/o/r/compare/main...sha1"]


def test_отставший_pr_не_мержится_даже_если_clean(площадка: Any) -> None:
    """Живой дефект: без защиты ветки площадка отдаёт `clean` для отставшего PR."""
    двойник = площадка(pulls=[_pull(1, mergeable_state="clean")], behind_by=1)

    merge_queue.run("o/r", "main", dry=False)

    пути = [путь for _, путь in двойник.записи]
    assert "/repos/o/r/pulls/1/update-branch" in пути
    assert _мержи(двойник) == []
