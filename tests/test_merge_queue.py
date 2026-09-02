"""Проверки обхода очереди (#8).

Обход имеет право мержить в `main`, поэтому проверяется он на подделанных
ответах площадки и в первую очередь — на том, чего делать **не** должен:
мержить дважды за прогон, останавливаться на конфликтном PR, ставить метку
второй раз.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest

import gh_rest
import merge_queue
import pr_check
import pr_ready

#: Проверки на голове PR. Обязательная `PR check` обязана быть среди них:
#: именно её очередь и требует (#46).
ПРОВЕРКИ = [
    {"name": "гейты", "status": "completed", "conclusion": "success"},
    {"name": pr_check.SELF_NAME, "status": "completed", "conclusion": "success"},
]


WORKFLOWS = Path(__file__).resolve().parents[1] / ".github" / "workflows"


def _pull(номер: int, **поля: Any) -> dict[str, Any]:
    основа: dict[str, Any] = {
        "number": номер,
        "state": "open",
        "draft": False,
        "mergeable": True,
        "mergeable_state": "clean",
        "body": "Closes #1",
        "labels": [{"name": "area/ci"}, {"name": "enhancement"}],
        "head": {
            "sha": f"sha{номер}",
            "ref": f"agent/ветка-{номер}",
            "repo": {"fork": False},
        },
        "base": {"ref": "main", "sha": "base-sha"},
    }
    основа.update(поля)
    return основа


def _снимок(pull: dict[str, Any]) -> pr_ready.Snapshot:
    return pr_ready.Snapshot(pull=pull, checks=list(ПРОВЕРКИ))


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
        отказ: frozenset[int] = frozenset(),
    ) -> None:
        self.pulls = {p["number"]: p for p in pulls}
        self.main_busy = main_busy
        self.main_red = main_red
        self.behind_by = behind_by
        self.отказ = отказ
        self.записи: list[tuple[str, str]] = []
        self.сравнения: list[str] = []

    def request(
        self, method: str, path: str, *, body: Any = None, params: Any = None
    ) -> Any:
        if method != "GET":
            self.записи.append((method, path))
            if path.endswith("/merge"):
                номер = int(path.rsplit("/", 2)[1])
                if номер in self.отказ:
                    raise gh_rest.GitHubError(
                        method,
                        path,
                        merge_queue.MERGE_REFUSED,
                        'Required status check "PR check" is expected',
                    )
            return {}
        if path.endswith("/jobs"):
            # Джобы прогона ci — ровно то, что создаётся и на общей ветке, и
            # на изменении. Джоба самой очереди здесь нет и быть не может.
            return {"jobs": [{"name": "гейты"}]}
        if path.endswith("/runs"):
            прогоны: list[dict[str, Any]] = (
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
                    "id": 777,
                }
            )
            return {"workflow_runs": прогоны}
        if "/compare/" in path:
            self.сравнения.append(path)
            return {"behind_by": self.behind_by, "ahead_by": 1}
        if path.endswith("/check-runs"):
            # Обязательная `PR check` здесь есть: без неё вердикт был бы
            # «ждать» у каждого PR, и подделка проверяла бы не то.
            return {"check_runs": list(ПРОВЕРКИ)}
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


def test_состояние_ветки_это_занятость_и_цвет(площадка: Any) -> None:
    """Эталона имён здесь больше нет — и это починка, а не упрощение (#46).

    Эталон брался с общей ветки и ломал любой PR, который состав проверок
    МЕНЯЕТ: подъём версии Python даёт другие имена, эталон приходит из
    прошлого, вердикт «джобы не созданы» становится вечным. Починить в PR
    нельзя: чтобы эталон обновился, изменение должно сначала уехать в `main`.

    Полноту набора теперь удостоверяет обязательная проверка `PR check` — она
    читает состав из дерева самого изменения.
    """
    площадка(pulls=[_pull(1)])

    busy, red = merge_queue.main_state("o/r", "main")

    assert not busy and not red


def test_состояние_ветки_спрашивается_у_ci_и_ничего_не_меняет(площадка: Any) -> None:
    """Спрашивается именно `ci`, а не «все прогоны на ветке».

    «Все прогоны» вернули бы и обход самой очереди: она ходит по общей ветке и
    оставляет там свои прогоны, к цвету ветки отношения не имеющие.
    """
    двойник = площадка(pulls=[_pull(1)])

    merge_queue.main_state("o/r", "main")

    пути = [путь for _, путь in двойник.записи]
    assert not пути, "чтение состояния ничего не меняет"


# ── очередь просыпается вовремя ───────────────────────────────────────────


def _имя_workflow(путь: Path) -> str:
    совпадение = re.search(r"^name: (.+)$", путь.read_text(encoding="utf-8"), re.M)
    assert совпадение, f"{путь.name}: у workflow нет имени"
    return совпадение.group(1).strip()


def _будильники_очереди() -> set[str]:
    """Имена workflow, от завершения которых очередь просыпается."""
    текст = (WORKFLOWS / "merge-queue.yml").read_text(encoding="utf-8")
    блок = re.search(r"^    workflows:\n((?:      - .+\n)+)", текст, re.M)
    assert блок, "в merge-queue.yml не нашёлся список workflows"
    return {с.strip()[2:].strip() for с in блок.group(1).splitlines()}


def test_очередь_просыпается_от_всех_проверок_pr() -> None:
    """Список будильников сверяется с деревом, а не ведётся вниманием.

    Последним на pull request зеленеет не `ci`, а обязательная проверка: она
    ждёт всех остальных по построению. Пока в списке стояло одно имя, момент
    готовности PR очередь не видела никогда — PR #41 простоял 50 минут при
    нуле обходов.

    Расхождение опасно в обе стороны: недостающее имя возвращает тот же
    простой, лишнее — обходы на событие, которое готовности не меняет.
    """
    по_pull_request = {
        _имя_workflow(путь)
        for путь in sorted(WORKFLOWS.glob("*.yml"))
        if re.search(r"^  pull_request:", путь.read_text(encoding="utf-8"), re.M)
    }

    assert по_pull_request, "не нашлось ни одного workflow, ходящего по pull_request"
    assert _будильники_очереди() == по_pull_request


# ── отказ площадки на мерже — не отказ обхода (#55) ───────────────────────


def test_отказ_по_правилам_площадки_не_красит_обход(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Площадка отказывает по своим правилам, а обход выходил кодом 2.

    Замер: на голове #54 лежал завершённый успешный `PR check`, а мерж
    отвечал «Required status check "PR check" is expected» — и полтора часа
    спустя ответ был тот же, из другого места тоже. Причина осталась
    ненайденной; известно только, что обход СВОЮ работу сделал верно.

    Красить общую ветку кодом «механизм не отработал» из-за отказа площадки
    значит приучать читать красное как шум.
    """

    def отказ(метод: str, путь: str, **прочее: Any) -> Any:
        raise gh_rest.GitHubError(
            метод, путь, 405, '{"message":"Required status check is expected."}'
        )

    monkeypatch.setattr(gh_rest, "request", отказ)

    поехал = merge_queue.merge("o/r", 54)

    assert поехал is False
    вывод = capsys.readouterr().out
    assert "::warning::" in вывод, "отказ обязан быть виден, а не проглочен"
    assert "#54 не поехал" in вывод
    assert "попробует снова" in вывод
    assert "расклинивается новой головой" in вывод, (
        "повторяющийся отказ обязан называть, чем он лечится"
    )


def test_чужая_ошибка_на_мерже_остаётся_отказом(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Тихого запасного пути нет: 403 и 500 — это поломка, а не «не в этот раз».

    Иначе очередь молча не мержила бы ничего при отозванных правах.
    """

    def отказ(метод: str, путь: str, **прочее: Any) -> Any:
        raise gh_rest.GitHubError(метод, путь, 403, '{"message":"Forbidden"}')

    monkeypatch.setattr(gh_rest, "request", отказ)

    with pytest.raises(gh_rest.GitHubError):
        merge_queue.merge("o/r", 54)


def test_удачный_мерж_отвечает_да(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gh_rest, "request", lambda *a, **k: {})

    assert merge_queue.merge("o/r", 54) is True


def test_застрявшая_голова_не_держит_очередь(
    площадка: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Отказ на голове пропускает её вперёд, а не запирает очередь навсегда.

    Первая редакция ломалась после отказа: «правило „не больше одного мержа“
    действует и когда первый не уехал». Рассуждение неверное. Правило про
    вытеснение ожидающего прогона на `main`, а не уехавший PR прогона на
    `main` не начинает — вытеснять нечем.

    Цена ошибки — та самая, от которой заведён весь этот заход. Отказ держался
    полтора часа и сам не проходит: расклинивает его новая голова, то есть
    человек или окно. До тех пор застрявший #54 не давал бы уехать ни одному
    PR за собой, а обход при этом был бы **зелёным**. Зелёное и стоящее хуже
    красного: красное видно.
    """
    двойник = площадка(pulls=[_pull(1), _pull(2)], отказ=frozenset({1}))

    merge_queue.run("o/r", "main", dry=False)

    assert _мержи(двойник) == [
        "/repos/o/r/pulls/1/merge",
        "/repos/o/r/pulls/2/merge",
    ], "после отказа на #1 очередь обязана попробовать #2"
    assert "::warning::" in capsys.readouterr().out


def test_после_удачного_мержа_очередь_всё_равно_останавливается(
    площадка: Any,
) -> None:
    """Проход дальше — только после отказа, и это не отмена правила.

    Уехавший PR начинает прогон на `main`; второй мерж вытеснил бы его, и тот
    не начался бы вовсе. Поэтому проверяется именно пара: отказ пускает
    очередь дальше, удача — останавливает.
    """
    двойник = площадка(pulls=[_pull(1), _pull(2), _pull(3)])

    merge_queue.run("o/r", "main", dry=False)

    assert _мержи(двойник) == ["/repos/o/r/pulls/1/merge"]


def test_отказ_по_всем_готовым_называется_вслух(
    площадка: Any, capsys: pytest.CaptureFixture[str]
) -> None:
    """Обход, не сдвинувший ничего, обязан сказать почему и чем это лечится.

    «двигать нечего» без перечня выглядело бы как пустая очередь — то есть
    как штатная работа.
    """
    площадка(pulls=[_pull(1), _pull(2)], отказ=frozenset({1, 2}))

    merge_queue.run("o/r", "main", dry=False)

    вывод = capsys.readouterr().out
    assert "#1, #2" in вывод, "перечень застрявших обязан быть в выводе"
    assert "новой головой" in вывод
