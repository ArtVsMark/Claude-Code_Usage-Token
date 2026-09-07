"""Закавыченный разделитель heredoc (правило 013).

Дороже всего здесь то, что дефект **молчаливый**: оболочка раскрывает
`$переменные` и обратные слэши в теле, записанное расходится с написанным, и
прогон при этом зелёный. Красное появляется где-то дальше и по другой причине.
"""

from __future__ import annotations

from pathlib import Path

import pytest

import shell_heredoc

КОРЕНЬ = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize(
    "строка",
    [
        "          cat <<'EOF' > файл",
        '          cat <<"EOF" > файл',
        "          python3 - <<'PY'",
        "          cat <<-'EOF'",
    ],
)
def test_закавыченный_разделитель_проходит(строка: str) -> None:
    находки, всего = shell_heredoc.проверить_текст(строка, "прогон.yml")
    assert находки == []
    assert всего == 1


@pytest.mark.parametrize(
    "строка",
    ["          cat <<EOF > файл", "          python3 - <<PY", "          cat <<-EOF"],
)
def test_голый_разделитель_отвергается(строка: str) -> None:
    находки, всего = shell_heredoc.проверить_текст(строка, "прогон.yml")
    assert всего == 1
    assert len(находки) == 1
    assert "без кавычек" in str(находки[0])


def test_находка_называет_файл_и_строку() -> None:
    находки, _ = shell_heredoc.проверить_текст("первая\nвторая <<EOF\n", "п.yml")
    assert находки[0].строка == 2
    assert находки[0].файл == "п.yml"
    assert "п.yml:2" in str(находки[0])


def test_сдвиг_влево_не_heredoc() -> None:
    """`<` и `<<` в тексте без разделителя предметом не являются."""
    находки, всего = shell_heredoc.проверить_текст("echo $((1 << 2))", "п.yml")
    assert (находки, всего) == ([], 0)


def test_прогоны_проекта_чисты() -> None:
    итог = shell_heredoc.проверить(КОРЕНЬ)
    assert итог.находки == []
    assert итог.прогонов > 0


def test_без_прогонов_нечего_проверять(tmp_path: Path) -> None:
    итог = shell_heredoc.проверить(tmp_path)
    assert (итог.находки, итог.прогонов) == ([], 0)


def test_код_возврата(tmp_path: Path) -> None:
    (tmp_path / shell_heredoc.WORKFLOWS).mkdir(parents=True)
    (tmp_path / shell_heredoc.WORKFLOWS / "п.yml").write_text(
        "jobs:\n  x:\n    steps:\n      - run: cat <<EOF\n", encoding="utf-8"
    )
    assert shell_heredoc.main([str(tmp_path)]) == shell_heredoc.EXIT_FAILED
    assert shell_heredoc.main([str(КОРЕНЬ)]) == 0
