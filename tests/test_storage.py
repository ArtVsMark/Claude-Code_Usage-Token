"""Хранилище замеров: готовность, порядок строк, честность пуша (#2).

Git здесь **настоящий**, а не подделанный: проверяется поведение слияния и
перебазирования, то есть ровно то, чего в подделке не будет. Правило каталога
037 говорит, что находка на подделке — гипотеза; здесь и зелёное на подделке
было бы гипотезой.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from claude_code_usage import storage


def _git(где: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(где), *args], check=True, capture_output=True)


@pytest.fixture
def хранилище(tmp_path: Path) -> Path:
    """Готовое хранилище: клон с bare-удалёнкой, файлом замеров и merge=union."""
    удалёнка = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(удалёнка)],
        check=True,
        capture_output=True,
    )
    склад = tmp_path / "store"
    subprocess.run(
        ["git", "clone", "-q", str(удалёнка), str(склад)],
        check=True,
        capture_output=True,
    )
    _git(склад, "checkout", "-q", "-b", "main")
    _git(склад, "config", "user.email", "t@e.st")
    _git(склад, "config", "user.name", "Тест")
    (склад / "samples").mkdir()
    (склад / storage.SAMPLES).write_text("", encoding="utf-8")
    (склад / ".gitattributes").write_text(storage.MERGE_RULE + "\n", encoding="utf-8")
    _git(склад, "add", "-A")
    _git(склад, "commit", "-qm", "подготовка")
    _git(склад, "push", "-q", "-u", "origin", "main")
    return склад


# ── путь ──────────────────────────────────────────────────────────────────


def test_флаг_важнее_переменной(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(storage.ENV_STORE, "/из/переменной")

    assert storage.store_path("/из/флага") == Path("/из/флага")


def test_переменная_работает_без_флага(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(storage.ENV_STORE, "/из/переменной")

    assert storage.store_path() == Path("/из/переменной")


def test_без_пути_отказ_называет_переменную(monkeypatch: pytest.MonkeyPatch) -> None:
    """Захардкоженный путь превратил бы инструмент в личный скрипт.

    Значит отказ обязан сказать, ЧЕМ его задать, — иначе посторонний человек
    упрётся в него и не поймёт, что делать.
    """
    monkeypatch.delenv(storage.ENV_STORE, raising=False)

    with pytest.raises(storage.StoreError) as отказ:
        storage.store_path()

    assert storage.ENV_STORE in str(отказ.value)
    assert "storage-setup" in str(отказ.value)


# ── готовность ────────────────────────────────────────────────────────────


def test_готовое_хранилище_молчит(хранилище: Path) -> None:
    assert storage.readiness(хранилище) == []


def test_нет_merge_union_отказ_до_первой_записи(хранилище: Path) -> None:
    """Самая дорогая из проверок: без этой строки конфликт приходит НЕ ПРИ
    ПЕРВОМ запуске, а через несколько замеров — там, где связать его с
    причиной уже некому (`docs/storage-setup.md`).
    """
    (хранилище / ".gitattributes").write_text("*.txt text\n", encoding="utf-8")

    беды = storage.readiness(хранилище)

    assert len(беды) == 1
    assert "merge=union" in беды[0]


def test_комментарий_за_правилом_не_ломает_проверку(хранилище: Path) -> None:
    (хранилище / ".gitattributes").write_text(
        f"{storage.MERGE_RULE}  # замеры\n", encoding="utf-8"
    )

    assert storage.readiness(хранилище) == []


def test_нет_файла_замеров_отказ(хранилище: Path) -> None:
    (хранилище / storage.SAMPLES).unlink()

    assert any("usage.jsonl" in беда for беда in storage.readiness(хранилище))


def test_не_git_отказ(tmp_path: Path) -> None:
    (tmp_path / "samples").mkdir()
    (tmp_path / storage.SAMPLES).write_text("", encoding="utf-8")
    (tmp_path / ".gitattributes").write_text(
        storage.MERGE_RULE + "\n", encoding="utf-8"
    )

    assert any("не git" in беда for беда in storage.readiness(tmp_path))


def test_беды_называются_все_разом(tmp_path: Path) -> None:
    """Чинить по одному пункту за запуск дороже, а забыть второй — обычное дело."""
    tmp_path.joinpath("пусто").mkdir()

    беды = storage.readiness(tmp_path / "пусто")

    assert len(беды) >= 2


# ── порядок строк ─────────────────────────────────────────────────────────


def test_прошлый_замер_это_максимум_а_не_хвост_файла() -> None:
    """`merge=union` объединяет, но НЕ СОРТИРУЕТ.

    Взять последнюю строку значило бы получить верный ответ на одном окне и
    молча неверный на двух — и работать это будет годами, пока второго окна нет.
    """
    строки = [
        {"ts": "2026-09-02T12:00:00Z", "source": "registry"},
        {"ts": "2026-09-02T15:00:00Z", "source": "registry"},
        {"ts": "2026-09-02T09:00:00Z", "source": "registry"},
    ]

    assert storage.last_ts(строки, source="registry") == "2026-09-02T15:00:00Z"


def test_источники_считаются_порознь() -> None:
    """Реестр и транскрипт меряют разные величины (#52) — и частота у них своя."""
    строки = [
        {"ts": "2026-09-02T15:00:00Z", "source": "registry"},
        {"ts": "2026-09-02T09:00:00Z", "source": "transcript"},
    ]

    assert storage.last_ts(строки, source="transcript") == "2026-09-02T09:00:00Z"


def test_пустое_хранилище_не_знает_прошлого() -> None:
    assert storage.last_ts([], source="registry") is None


# ── чтение и запись ───────────────────────────────────────────────────────


def test_битая_строка_не_роняет_чтение(хранилище: Path) -> None:
    """Файл переживает несколько версий инструмента и слияния из разных окон."""
    (хранилище / storage.SAMPLES).write_text(
        '{"ts":"1","source":"registry"}\nне json\n{"ts":"2","source":"registry"}\n',
        encoding="utf-8",
    )

    assert len(storage.read_rows(хранилище)) == 2


def test_запись_только_дописывает(хранилище: Path) -> None:
    storage.append(хранилище, [{"ts": "1"}])
    storage.append(хранилище, [{"ts": "2"}])

    строки = (хранилище / storage.SAMPLES).read_text(encoding="utf-8").splitlines()
    assert [json.loads(с)["ts"] for с in строки] == ["1", "2"]


# ── честность пуша ────────────────────────────────────────────────────────


def test_замер_уезжает(хранилище: Path) -> None:
    строки = [{"ts": "2026-09-02T15:00:00Z", "source": "registry", "input": 1}]
    storage.append(хранилище, строки)
    storage.commit(хранилище, "замер")

    итог = storage.push(хранилище)

    assert итог.pushed is True
    assert storage.confirm(хранилище, строки) == []


def test_отказ_пуша_не_потеря_и_так_и_сказано(хранилище: Path) -> None:
    """Строка уже в файле и в истории — сообщение обязано это назвать.

    «Не уехало» и «потеряно» требуют разных действий человека.
    """
    _git(хранилище, "remote", "set-url", "origin", "/нет/такого/пути")
    строки = [{"ts": "2026-09-02T15:00:00Z", "source": "registry"}]
    storage.append(хранилище, строки)
    storage.commit(хранилище, "замер")

    итог = storage.push(хранилище, attempts=1)

    assert итог.pushed is False
    assert "не потерян" in итог.detail
    assert storage.confirm(хранилище, строки) == [], "строка обязана остаться в файле"


def test_коммитить_нечего_это_не_ошибка(хранилище: Path) -> None:
    assert storage.commit(хранилище, "пусто") is False


def test_сверка_видит_пропавшую_строку(хранилище: Path) -> None:
    """Код возврата `git push` не доказывает, что наши строки на месте.

    Между «дописали» и «отправили» лежит перебазирование, которое вправе
    выбросить коммит целиком.
    """
    пропавшая = {"ts": "2026-09-02T15:00:00Z", "source": "registry"}

    assert storage.confirm(хранилище, [пропавшая]) == [пропавшая]


def test_два_окна_пишут_одновременно_и_обе_строки_на_месте(
    хранилище: Path, tmp_path: Path
) -> None:
    """Единственная проверка, которую `docs/storage-setup.md` называет обязательной.

    Второе окно пишет, НЕ подтянув первое: именно так и бывает, когда оба окна
    работают. Ожидание — обе строки и ни одного конфликта.
    """
    второе = tmp_path / "store2"
    subprocess.run(
        ["git", "clone", "-q", str(tmp_path / "remote.git"), str(второе)],
        check=True,
        capture_output=True,
    )
    _git(второе, "config", "user.email", "b@e.st")
    _git(второе, "config", "user.name", "Б")

    первая = [{"ts": "2026-09-02T15:00:00Z", "source": "registry", "input": 1}]
    вторая = [{"ts": "2026-09-02T15:00:01Z", "source": "registry", "input": 2}]
    storage.append(хранилище, первая)
    storage.commit(хранилище, "замер A")
    assert storage.push(хранилище).pushed
    storage.append(второе, вторая)
    storage.commit(второе, "замер B")
    assert storage.push(второе).pushed

    итог = tmp_path / "verify"
    subprocess.run(
        ["git", "clone", "-q", str(tmp_path / "remote.git"), str(итог)],
        check=True,
        capture_output=True,
    )
    записанные = storage.read_rows(итог)
    assert len(записанные) == 2, "union обязан дать обе строки, а не одну"
    assert {с["input"] for с in записанные} == {1, 2}
