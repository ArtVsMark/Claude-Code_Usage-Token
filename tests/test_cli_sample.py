"""Команда `sample`: сборка, белый список, отказы, частота (#2)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

import pytest

from claude_code_usage import cli, storage, whitelist

ЗАПИСЬ: dict[str, Any] = {
    "id": "session_A",
    "title": "текст, который писал человек",
    "external_metadata": {
        "rate_limit_info": {
            "isUsingOverage": False,
            "rateLimitType": "five_hour",
            "resetsAt": 1788370800,
            "status": "allowed",
        },
        "usage": {
            "cache_read_tokens": 100,
            "cache_write_tokens": 20,
            "cost_usd": 1.5,
            "input_tokens": 5,
            "output_tokens": 7,
        },
    },
}


def _git(где: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(где), *args], check=True, capture_output=True)


@pytest.fixture
def склад(tmp_path: Path) -> Path:
    удалёнка = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "-q", "--bare", "-b", "main", str(удалёнка)],
        check=True,
        capture_output=True,
    )
    место = tmp_path / "store"
    subprocess.run(
        ["git", "clone", "-q", str(удалёнка), str(место)],
        check=True,
        capture_output=True,
    )
    _git(место, "checkout", "-q", "-b", "main")
    _git(место, "config", "user.email", "t@e.st")
    _git(место, "config", "user.name", "Тест")
    (место / "samples").mkdir()
    (место / storage.SAMPLES).write_text("", encoding="utf-8")
    (место / ".gitattributes").write_text(storage.MERGE_RULE + "\n", encoding="utf-8")
    _git(место, "add", "-A")
    _git(место, "commit", "-qm", "подготовка")
    _git(место, "push", "-q", "-u", "origin", "main")
    return место


@pytest.fixture
def выгрузка(tmp_path: Path) -> Path:
    файл = tmp_path / "registry.json"
    файл.write_text(json.dumps({"ccr": {"data": [ЗАПИСЬ]}}), encoding="utf-8")
    return файл


def _замер(выгрузка: Path, склад: Path, *ещё: str) -> list[str]:
    return ["sample", "--registry", str(выгрузка), "--store", str(склад), *ещё]


# ── отказы ────────────────────────────────────────────────────────────────


def test_без_источников_отказ_называет_почему_не_сам(
    склад: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Реестр отдаёт MCP, то есть окно; инструмент до него не дотягивается.

    Отказ обязан это сказать, иначе человек будет искать флаг «сходить в
    реестр», которого нет и быть не может.
    """
    assert cli.main(["sample", "--store", str(склад)]) == cli.EXIT_USAGE

    assert "MCP" in capsys.readouterr().err


def test_без_хранилища_отказ(
    выгрузка: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv(storage.ENV_STORE, raising=False)

    assert cli.main(["sample", "--registry", str(выгрузка)]) == cli.EXIT_USAGE

    assert storage.ENV_STORE in capsys.readouterr().err


def test_неготовое_хранилище_отказ_до_записи(
    выгрузка: Path, склад: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (склад / ".gitattributes").unlink()

    assert cli.main(_замер(выгрузка, склад)) == cli.EXIT_USAGE

    assert "merge=union" in capsys.readouterr().err
    assert storage.read_rows(склад) == [], "неготовое хранилище не должно быть тронуто"


def test_незнакомое_поле_расхода_роняет_замер_и_ничего_не_пишет(
    tmp_path: Path, склад: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Новое слагаемое расхода, записанное мимо, занижает сумму навсегда:
    хранилище append-only, дописать потом нечем (#7, #18).
    """
    порченая = json.loads(json.dumps(ЗАПИСЬ))
    порченая["external_metadata"]["usage"]["мысли_модели"] = 1
    файл = tmp_path / "плохая.json"
    файл.write_text(json.dumps([порченая]), encoding="utf-8")

    assert cli.main(_замер(файл, склад)) == cli.EXIT_FAILED

    assert "мысли_модели" in capsys.readouterr().err
    assert storage.read_rows(склад) == []


def test_неполный_usage_роняет_замер(
    tmp_path: Path, склад: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    урезанная = json.loads(json.dumps(ЗАПИСЬ))
    del урезанная["external_metadata"]["usage"]["cost_usd"]
    файл = tmp_path / "урезанная.json"
    файл.write_text(json.dumps([урезанная]), encoding="utf-8")

    assert cli.main(_замер(файл, склад)) == cli.EXIT_FAILED

    assert storage.read_rows(склад) == []


def test_ни_одной_сессии_с_расходом_это_отказ(
    tmp_path: Path, склад: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Пустая строка неотличима от нулевого расхода — и отравила бы шкалу."""
    файл = tmp_path / "мосты.json"
    файл.write_text(
        json.dumps([{"id": "мост", "external_metadata": {}}]), encoding="utf-8"
    )

    assert cli.main(_замер(файл, склад)) == cli.EXIT_FAILED

    assert "нечего" in capsys.readouterr().err


def test_нечитаемая_выгрузка_отказ_а_не_молчание(
    tmp_path: Path, склад: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    файл = tmp_path / "битая.json"
    файл.write_text("{не json", encoding="utf-8")

    assert cli.main(_замер(файл, склад)) == cli.EXIT_FAILED
    assert "не прочитать" in capsys.readouterr().err


# ── что попадает в строку ─────────────────────────────────────────────────


def test_в_строке_только_белый_список(
    выгрузка: Path, склад: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ни имени сессии, ни задачи, ни ветки. Хранилище приватное, но утечка в
    приватный репозиторий всё равно утечка.
    """
    assert cli.main(_замер(выгрузка, склад, "--dry-run")) == 0

    строка = json.loads(
        next(с for с in capsys.readouterr().out.splitlines() if с.startswith("{"))
    )
    assert set(строка) <= whitelist.SAMPLE_FIELDS
    assert "текст, который писал человек" not in json.dumps(строка, ensure_ascii=False)


def test_dry_run_ничего_не_пишет(выгрузка: Path, склад: Path) -> None:
    assert cli.main(_замер(выгрузка, склад, "--dry-run")) == 0

    assert storage.read_rows(склад) == []


def test_неполнота_выгрузки_попадает_в_вывод(
    tmp_path: Path, склад: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Погрешность — часть вывода, а не отладка (`CLAUDE.md` § «Правила измерения»)."""
    файл = tmp_path / "страница.json"
    файл.write_text(
        json.dumps({"ccr": {"data": [ЗАПИСЬ], "has_more": True}}), encoding="utf-8"
    )

    cli.main(_замер(файл, склад, "--dry-run"))

    assert "НЕПОЛНАЯ" in capsys.readouterr().out


# ── запись ────────────────────────────────────────────────────────────────


def test_замер_записывается_и_уезжает(выгрузка: Path, склад: Path) -> None:
    assert cli.main(_замер(выгрузка, склад)) == 0

    записанные = storage.read_rows(склад)
    assert len(записанные) == 1
    assert записанные[0]["source"] == "registry"
    assert записанные[0]["input"] == 5


def test_дважды_подряд_не_ломает_файл(выгрузка: Path, склад: Path) -> None:
    """Требование #2 дословно: строки просто дописываются."""
    assert cli.main(_замер(выгрузка, склад, "--min-interval", "0")) == 0
    assert cli.main(_замер(выгрузка, склад, "--min-interval", "0")) == 0

    assert len(storage.read_rows(склад)) >= 1


def test_порог_частоты_не_даёт_писать_чаще(
    выгрузка: Path, склад: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """Иначе коммиты пойдут пачками, а история замеров превратится в шум."""
    cli.main(_замер(выгрузка, склад, "--min-interval", "0"))
    было = len(storage.read_rows(склад))

    assert cli.main(_замер(выгрузка, склад)) == 0

    assert "рано" in capsys.readouterr().out
    assert len(storage.read_rows(склад)) == было


def test_порог_снимается_нулём(выгрузка: Path, склад: Path) -> None:
    cli.main(_замер(выгрузка, склад, "--min-interval", "0"))
    было = len(storage.read_rows(склад))

    cli.main(_замер(выгрузка, склад, "--min-interval", "0"))

    assert len(storage.read_rows(склад)) == было + 1


def test_no_push_оставляет_локально(выгрузка: Path, склад: Path) -> None:
    assert cli.main(_замер(выгрузка, склад, "--no-push")) == 0

    assert len(storage.read_rows(склад)) == 1


def test_метка_времени_в_формате_спецификации() -> None:
    import datetime as dt

    метка = cli.now_stamp(dt.datetime(2026, 9, 2, 15, 9, 3, tzinfo=dt.UTC))

    assert метка == "2026-09-02T15:09:03Z"


def test_протёкшее_поле_не_доходит_до_файла(
    выгрузка: Path,
    склад: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Второй заслон перед записью — и его нельзя оставить непроверяемым.

    Сборщик отдаёт только разрешённые поля, поэтому обычным путём этот отказ не
    вызвать: мутация «убрать проверкубелого списка» проходила незамеченной, то
    есть заслон был гейтом, который нельзя провалить (правило 075).

    Здесь подделан именно СБОРЩИК, а не реестр, и подделан намеренно: заслон
    существует ровно на случай, когда сборщик изменили, а белый список забыли.
    Это не выдуманный случай — поле `source` в него добавляли на прошлой неделе.
    """

    def протекающий(*_: object, ts: str, session_id: str) -> dict[str, Any]:
        return {"ts": ts, "session": session_id, "source": "registry", "task": "секрет"}

    monkeypatch.setattr(whitelist, "build_sample", протекающий)

    assert cli.main(_замер(выгрузка, склад)) == cli.EXIT_FAILED

    err = capsys.readouterr().err
    assert "task" in err
    assert "белого списка" in err
    assert storage.read_rows(склад) == [], "ни одна строка не должна быть записана"
