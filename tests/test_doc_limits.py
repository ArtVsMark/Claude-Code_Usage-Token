"""Предел растущего документа (правило 108).

Дороже всего здесь **предел, краснеющий назавтра**: его приучают обходить, а не
соблюдать (051). Поэтому набор проверяет и то, что у своих документов есть
запас, — не только то, что переполнение ловится.
"""

from __future__ import annotations

from pathlib import Path

import doc_limits

КОРЕНЬ = Path(__file__).resolve().parents[1]


def test_свои_документы_в_пределах() -> None:
    итог = doc_limits.проверить(КОРЕНЬ)
    assert итог.находки == []
    assert итог.проверено == len(doc_limits.ПРЕДЕЛЫ)


def test_у_каждого_предела_назван_выход() -> None:
    """Отказ, сообщающий о проблеме и молчащий о выходе, — половина работы."""
    for предел in doc_limits.ПРЕДЕЛЫ:
        assert предел.выход.strip(), предел.путь
        assert предел.предел > 0


def test_переполнение_называет_выход(tmp_path: Path) -> None:
    (tmp_path / "CLAUDE.md").write_text("строка\n" * 601, encoding="utf-8")
    находки = doc_limits.проверить(tmp_path).находки
    assert len(находки) == 1
    assert "601 строк при пределе 600" in находки[0]
    assert "Что делать:" in находки[0]


def test_разделы_считаются_а_не_строки(tmp_path: Path) -> None:
    (tmp_path / "CHANGELOG.md").write_text(
        "## a\n\n## b\n\n## c\n\n## d\n", encoding="utf-8"
    )
    находки = doc_limits.проверить(tmp_path).находки
    assert len(находки) == 1
    assert "4 разделов версий при пределе 3" in находки[0]


def test_документа_нет_предел_не_о_чем(tmp_path: Path) -> None:
    итог = doc_limits.проверить(tmp_path)
    assert (итог.находки, итог.проверено) == ([], 0)


def test_называется_самый_полный(tmp_path: Path) -> None:
    """Число видно до отказа: его рост и есть сигнал."""
    (tmp_path / "HISTORY.md").write_text("строка\n" * 199, encoding="utf-8")
    итог = doc_limits.проверить(tmp_path)
    assert итог.находки == []
    assert итог.самый_полный == "HISTORY.md 199/200"


def test_код_возврата(tmp_path: Path) -> None:
    (tmp_path / "HISTORY.md").write_text("строка\n" * 201, encoding="utf-8")
    assert doc_limits.main([str(tmp_path)]) == doc_limits.EXIT_FAILED
    assert doc_limits.main([str(КОРЕНЬ)]) == 0
