"""Перепись ссылок: адрес называет нынешнее имя (переименование 2026-09-02, #56)."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import repo_links

#: Владелец подделки НАМЕРЕННО не тот, что у проекта.
#:
#: Гейт ходит по дереву под версией, а этот файл в нём лежит: с настоящим
#: владельцем подделки стали бы находками, и `preflight` покраснел бы на
#: собственных тестах. Ровно эта ловушка уже была у проверки на секреты —
#: там она решена символьным классом внутри литерала (`gh[p]_`).
#:
#: Покрытие от этого не страдает: набор владельцев гейт берёт из переданного
#: списка, поэтому «свой владелец, имя не из списка» проверяется полностью.
СПИСОК = frozenset({"Vladelets/Novoye-Imya", "Vladelets/Sosed"})


def _находки(текст: str, *, адреса: frozenset[str] = СПИСОК) -> list[str]:
    return [н.адрес for н in repo_links.check_text(текст, "ф.md", адреса=адреса)]


def test_старое_имя_в_ссылке_находится() -> None:
    """Ровно инцидент: репозиторий переименован, ссылка осталась прежней.

    Редирект GitHub оставляет её рабочей, поэтому не краснеет ничто — ни
    прогон, ни открывшаяся страница. Найти это может только перепись.
    """
    assert _находки("см. https://github.com/Vladelets/staroye-imya/issues/1") == [
        "Vladelets/staroye-imya"
    ]


def test_нынешнее_имя_молчит() -> None:
    assert (
        _находки("https://github.com/ArtVsMark/Claude-Code_Usage-Token/issues/1") == []
    )


def test_регистр_имени_не_считается_расхождением() -> None:
    """GitHub регистр в имени не различает, и краснеть на нём — ложный отказ."""
    assert _находки("https://github.com/VLADELETS/novoye-imya") == []


def test_значок_разбирается_после_кодирования() -> None:
    """В значке витрины адрес лежит в параметре `url=`, косые заэкранированы.

    Первая редакция выражения ловила только сырые косые — и значок, самый
    заметный элемент витрины, оставался бы непроверенным.
    """
    значок = (
        "[![Версия](https://img.shields.io/endpoint?url=https%3A%2F%2F"
        "raw.githubusercontent.com%2FVladelets%2Fstaroye-imya%2Fmain"
        "%2F.github%2Fbadges%2Fversion.json)](pyproject.toml)"
    )
    assert _находки(значок) == ["Vladelets/staroye-imya"]


def test_действие_в_workflow_находится_хотя_это_не_ссылка() -> None:
    """`uses:` — не URL, и ставка на нём выше всех остальных.

    Пока старое имя свободно, редирект тянет наше же действие. Как только имя
    займёт кто угодно другой, тот же `uses:` потянет ЧУЖОЙ код — а
    `rules-inbox.yml` выдаёт ему `issues: write`.
    """
    assert _находки("      - uses: Vladelets/staryy-katalog@v1.1.0") == [
        "Vladelets/staryy-katalog"
    ]


def test_чужой_владелец_не_трогается() -> None:
    """Инвентарь зависимостей — отдельная задача, и в белый список он не влезет."""
    assert _находки("      - uses: actions/checkout@v4") == []
    assert _находки("https://github.com/anthropics/claude-code") == []


def test_смежный_из_списка_разрешён() -> None:
    assert _находки("https://github.com/Vladelets/Sosed/issues/7") == []


def test_переименование_без_правки_списка_краснеет_в_прогоне() -> None:
    """Живой источник имени один и только в прогоне — `GITHUB_REPOSITORY`.

    Он же единственный якорь у самого списка: без этой сверки список устарел
    бы молча вместе со ссылками.
    """
    отказ = repo_links.список_свежий(СПИСОК, из_прогона="Vladelets/Tretye-Imya")

    assert отказ is not None
    assert "Tretye-Imya" in отказ
    assert "repo_links.py" in отказ, "отказ обязан называть, куда вписать имя"


def test_вне_прогона_список_не_сверяется() -> None:
    """Локально живого источника имени нет — и выдумывать его нельзя.

    Первая редакция брала имя из `git remote` как «живой источник». Оно тоже
    устаревает: клон, сделанный до переименования, хранит старый адрес, git
    его не обновляет, и работает он по тому же редиректу. Гейт тогда уверенно
    сообщал бы обратное тому, что есть.
    """
    assert repo_links.список_свежий(СПИСОК, из_прогона="") is None


def test_имя_из_прогона_разрешено() -> None:
    assert repo_links.список_свежий(СПИСОК, из_прогона="VLADELETS/novoye-imya") is None


def test_дерево_проекта_чистое() -> None:
    """Не подделка: настоящее дерево под версией.

    Замер 2026-09-02 на дереве до правки — 28 находок, включая строку `uses:`.
    """
    корень = Path(__file__).resolve().parents[1]

    assert repo_links.check_tree(корень) == []


def test_свой_адрес_есть_в_списке() -> None:
    """Список без собственного имени пропустил бы все свои же ссылки."""
    assert "ArtVsMark/Claude-Code_Usage-Token" in repo_links.АДРЕСА


def test_гейт_отдаёт_ненулевой_код() -> None:
    """Гейт, который нельзя провалить, — не гейт (правило 075)."""
    ответ = subprocess.run(
        [sys.executable, "scripts/repo_links.py"],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True,
        text=True,
        env={
            "GITHUB_REPOSITORY": "ArtVsMark/Ne-Sushchestvuyet",
            "PATH": "/usr/bin:/bin",
        },
    )

    assert ответ.returncode == repo_links.EXIT_FAILED
    assert "Ne-Sushchestvuyet" in ответ.stderr


def test_подделка_не_попадает_в_перепись_самого_дерева() -> None:
    """Владелец подделки разведён с настоящим — и это не оформление.

    Гейт ходит по файлам под версией, а этот файл среди них. С настоящим
    владельцем в подделках `preflight` краснел бы на собственных тестах, и
    первым же действием их бы «починили» — ослабив гейт.

    Тест держит развод: он упадёт, если подделку вернут на своего владельца.
    """
    свои = {имя.split("/", 1)[0].lower() for имя in repo_links.АДРЕСА}
    подделочные = {имя.split("/", 1)[0].lower() for имя in СПИСОК}

    assert not (свои & подделочные), (
        "владелец подделки совпал с настоящим — гейт найдёт собственные тесты"
    )
