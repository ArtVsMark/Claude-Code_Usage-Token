"""Свои потоки говорят UTF-8, а не то, что решила локаль (#63).

Отдельным файлом, а не хвостом `test_preflight.py`: дом у функции теперь свой
— общий модуль, которым пользуются все гейты, а не одна команда. Тот же довод,
что развёл витрину и `preflight`: файл, в конец которого дописывают все,
становится точкой конфликта независимо от того, насколько независимы правки.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import utf8_output


def test_потоки_переводятся_в_utf8(monkeypatch: pytest.MonkeyPatch) -> None:
    """Отчёт обязан оставаться читаемым при узкой кодировке потока.

    На Windows кодировка берётся из локали и UTF-8 не является. Падения нет:
    поток заменяет непредставимое молча — и команда, смысл которой в том, чтобы
    **назвать** отказавшее, перестаёт называть что-либо, оставаясь формально
    работающей. Это хуже отказа: отказ виден.
    """
    import io

    узкий_вывод = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    узкий_отказ = io.TextIOWrapper(io.BytesIO(), encoding="ascii")
    monkeypatch.setattr("sys.stdout", узкий_вывод)
    monkeypatch.setattr("sys.stderr", узкий_отказ)

    utf8_output.force_utf8_output()

    assert узкий_вывод.encoding == "utf-8"
    assert узкий_отказ.encoding == "utf-8"


def test_перевод_потоков_переживает_подменённый_поток(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Поток без `reconfigure` не должен ронять команду.

    Под перехватом вывода (pytest, некоторые обёртки CI) `sys.stdout` — не
    файловый поток, и метода у него нет. Падать из-за этого нельзя: проверки
    важнее кодировки.
    """
    monkeypatch.setattr("sys.stdout", object())
    monkeypatch.setattr("sys.stderr", object())
    utf8_output.force_utf8_output()


def test_кириллица_доходит_до_узкого_потока_читаемой(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Проверка читаемости, выполняемая **на той самой ОС**, где риск.

    Заменяет чтение лога прогона: логи заданий отдаются с хоста, закрытого
    политикой прокси, а этот тест едет в каждой ячейке матрицы, включая три
    ячейки Windows, и проверяет то же самое надёжнее — байтами, а не глазами.

    Без `force_utf8_output` запись кириллицы в поток с узкой кодировкой
    падает с `UnicodeEncodeError`, поэтому тест обязан упасть, если правку
    снимут.
    """
    import io

    буфер = io.BytesIO()
    поток = io.TextIOWrapper(буфер, encoding="ascii")
    monkeypatch.setattr("sys.stdout", поток)
    monkeypatch.setattr("sys.stderr", поток)

    utf8_output.force_utf8_output()

    строка = "✗ тесты (весь набор) — не прошло"
    поток.write(строка)
    поток.flush()

    assert буфер.getvalue().decode("utf-8") == строка
