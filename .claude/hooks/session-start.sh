#!/bin/bash
# Старт облачного окна: окружение проверки на планке проекта и Python 3.14 (#126).
#
# ТОЛЬКО В ОБЛАКЕ. На машине владельца окружение его, и ставить туда
# интерпретатор без спроса хук не вправе: признак облака — CLAUDE_CODE_REMOTE.
#
# ЗАЧЕМ. Облачное окно стартует без того, что нужно для `preflight` перед
# толчком: системный python3 — 3.11, ниже планки, а системный pip 24.0 не
# умеет `--group` (PEP 735). Прежде это собиралось руками и стояло отдельным
# пунктом эстафеты между окнами — то есть держалось вниманием.
#
# ОТКУДА 3.14. Образ несёт 3.10–3.13, встроенный uv знает лишь предрелиз 3.14,
# а сайт установщика uv (astral.sh) закрыт сетевой политикой. PyPI открыт,
# поэтому свежий uv ставится из PyPI. Замер 2 октября: `uv python install 3.14`
# ставит 3.14.8 меньше чем за 2 с.
#
# ПЛАНКА ЧИТАЕТСЯ ИЗ pyproject.toml, а не вписана сюда: сдвинется
# `requires-python` — хук сам соберёт окружение на новой версии. Своего гейта,
# читающего планку, в проекте нет, поэтому разбор здесь первый, а не второй.
#
# СБОЙ СЕТИ НЕ РОНЯЕТ СТАРТ. Всякий отказ — предупреждение с названным шагом и
# выход 0: окно открывается всегда, не готово только то, что названо. `.venv`
# попадает в PATH, только если собран целиком.
set -uo pipefail

warn() {
  echo "старт окна: $1 — окно работает без этого; preflight запускайте интерпретатором не ниже планки вручную" >&2
}

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
cd "${CLAUDE_PROJECT_DIR:-}" || { warn "нет каталога проекта"; exit 0; }

# Планка — тем же разбором, что у обёртки стража, и без Python: системный
# python3 окна ниже планки, и разбор на нём держал бы хуки в его грамматике
# (правило 217, #154). Один разбор на оба хука — .claude/hooks/floor.sh.
. "$(dirname "$0")/floor.sh"
floor=$(planka_floor pyproject.toml)
[ -n "$floor" ] || { warn "планка requires-python не прочитана"; exit 0; }

if ! command -v "python$floor" >/dev/null 2>&1; then
  { python3 -m venv /opt/uv \
      && /opt/uv/bin/pip install -q -U uv \
      && /opt/uv/bin/uv python install "$floor" \
      && ln -sf "$(/opt/uv/bin/uv python find "$floor")" "/usr/local/bin/python$floor"; } \
    || { warn "Python $floor не поставлен"; exit 0; }
fi

if [ ! -x .venv/bin/python ] \
  || [ "$(.venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])')" != "$floor" ]; then
  rm -rf .venv
  "python$floor" -m venv .venv || { warn "окружение на $floor не собрано"; exit 0; }
fi

# `--group dev` — PEP 735, его понимает pip от 25.1: сначала обновить pip, как
# и в прогонах. Повторный запуск дешёвый: всё уже стоит, pip только сверяет.
{ .venv/bin/pip install -q -U pip && .venv/bin/pip install -q -e . --group dev; } \
  || { warn "зависимости проверки не поставлены"; exit 0; }

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$CLAUDE_PROJECT_DIR/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
fi
exit 0
