"""Авто-мерж площадки: взвести, снять и пробу до доверия (#165).

## Зачем отдельный модуль

Взвод — единственная причина, по которой проекту разрешён GraphQL
(`gh_rest.ГРАФQL_ОПЕРАЦИИ`). Тексты мутаций живут здесь, в одном месте, и их
же позовёт очередь (#165, шаг 3): проба проверяет ровно то, чем потом будут
пользоваться, а не свою копию.

## Проба до доверия

Площадка принимает в мутации заголовок и тело будущего squash-коммита. От тела
зависит подпись в истории (правило 123): если площадка его не примет или
перепишет, коммиты поедут без подписи — так уже было с #108–#110, когда
поведение площадки предположили, а не замерили. Поэтому до того, как очередь
начнёт взводить, ручной прогон взводит **названный** PR, читает взвод обратно
по REST (`auto_merge` у PR) и сразу снимает. Приём — `arm.py --probe` у
проекта механизмов.

## Предохранитель

Взведённый PR площадка сольёт, как только обязательная проверка позеленеет.
Проба поэтому идёт только на PR, у которого `PR check` на голове уже
**красный**: до новой головы он не позеленеет, а взвод с `expectedHeadOid` на
новую голову не переедет. Зелёный, ожидающий или отсутствующий `PR check` —
отказ до первого обращения к GraphQL. Снятие стоит в `finally`.
"""

import argparse
import pathlib
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gh_rest
import merge_queue
import pr_check
from utf8_output import force_utf8_output

EXIT_OK = 0
#: Площадка взвод приняла, но с другим заголовком или телом, — находка:
#: очередь на этом доверии строить нельзя.
EXIT_FINDING = 1
#: Не отработала: PR не годится для пробы, площадка отказала или не ответила.
EXIT_BROKEN = 2

ВЗВЕСТИ = """mutation($id: ID!, $oid: GitObjectID!, $title: String!, $body: String!) {
  enablePullRequestAutoMerge(input: {
    pullRequestId: $id, expectedHeadOid: $oid, mergeMethod: SQUASH,
    commitHeadline: $title, commitBody: $body
  }) { clientMutationId }
}"""

СНЯТЬ = """mutation($id: ID!) {
  disablePullRequestAutoMerge(input: {pullRequestId: $id}) { clientMutationId }
}"""


@dataclass(frozen=True)
class Взвод:
    """Что просим у площадки: какой PR, на какой голове и с каким сообщением."""

    node_id: str
    head_sha: str
    заголовок: str
    тело: str


def взвести(взвод: Взвод) -> None:
    """Взвести авто-мерж. Голова закреплена: на новую голову взвод не переедет."""
    gh_rest.graphql(
        ВЗВЕСТИ,
        {
            "id": взвод.node_id,
            "oid": взвод.head_sha,
            "title": взвод.заголовок,
            "body": взвод.тело,
        },
    )


def снять(node_id: str) -> None:
    """Снять авто-мерж."""
    gh_rest.graphql(СНЯТЬ, {"id": node_id})


def заголовок_уплотнения(pull: dict[str, Any]) -> str:
    """Заголовок squash-коммита в форме площадки: «<заголовок PR> (#N)»."""
    return f"{pull.get('title', '')} (#{pull.get('number')})"


def цвет_проверки(repo: str, sha: str) -> str:
    """Итог обязательной проверки на коммите: `failure`, `success`, `нет`, ….

    Берётся последний check-run с этим именем: после обновления ветки площадка
    создаёт второй комплект, и считать надо по уникальным именам (CLAUDE.md).
    """
    # Все страницы: «последний» — это максимум по всем записям, а не по
    # первой сотне (212).
    записи = gh_rest.paged(
        f"/repos/{repo}/commits/{sha}/check-runs",
        params={"check_name": pr_check.SELF_NAME},
        key="check_runs",
    )
    прогоны = [
        r for r in записи if isinstance(r, dict) and r.get("name") == pr_check.SELF_NAME
    ]
    if not прогоны:
        return "нет"
    последний = max(прогоны, key=lambda r: int(r.get("id") or 0))
    if последний.get("status") != "completed":
        return "ожидает"
    return str(последний.get("conclusion") or "нет")


def расхождения(взвод: Взвод, auto_merge: Any) -> list[str]:
    """Чем взвод, прочитанный обратно, отличается от запрошенного."""
    if not isinstance(auto_merge, dict):
        return [f"взвод не виден по REST: auto_merge={auto_merge!r}"]
    найдено = []
    if auto_merge.get("merge_method") != "squash":
        найдено.append(f"способ {auto_merge.get('merge_method')!r}, а не squash")
    if auto_merge.get("commit_title") != взвод.заголовок:
        найдено.append(
            f"заголовок {auto_merge.get('commit_title')!r} вместо {взвод.заголовок!r}"
        )
    if (auto_merge.get("commit_message") or "").strip() != взвод.тело.strip():
        найдено.append("тело переписано площадкой — подпись в истории под угрозой")
    return найдено


def probe(repo: str, number: int) -> int:
    """Взвести названный PR, прочитать взвод обратно и снять."""
    pull = gh_rest.request("GET", f"/repos/{repo}/pulls/{number}")
    if pull.get("state") != "open":
        print(f"#{number}: PR не открыт — пробовать не на чем")
        return EXIT_BROKEN
    sha = str(pull["head"]["sha"])
    цвет = цвет_проверки(repo, sha)
    if цвет != "failure":
        print(
            f"#{number}: «{pr_check.SELF_NAME}» на {sha[:7]} — {цвет}, а нужен "
            "красный. Взведённый PR площадка сольёт по зелёному, поэтому проба "
            "идёт только на PR, который до новой головы слить нельзя"
        )
        return EXIT_BROKEN

    тело = merge_queue.squash_message(repo, number)
    if тело is None:
        print(f"#{number}: тело уплотнения не собрано — проверять нечего")
        return EXIT_BROKEN
    взвод = Взвод(str(pull["node_id"]), sha, заголовок_уплотнения(pull), тело)

    # Снимается только принятый взвод: снятие несуществующего — второй отказ,
    # и он заслонил бы первый. Остаток при любом исходе ловит чтение ниже.
    взвести(взвод)
    try:
        прочитанный = gh_rest.request("GET", f"/repos/{repo}/pulls/{number}")
        найдено = расхождения(взвод, (прочитанный or {}).get("auto_merge"))
    finally:
        снять(взвод.node_id)

    снятый = gh_rest.request("GET", f"/repos/{repo}/pulls/{number}")
    if (снятый or {}).get("auto_merge") is not None:
        print(f"::error::#{number}: взвод не снялся — снимите руками")
        return EXIT_BROKEN

    if найдено:
        for строка in найдено:
            print(f"::error::#{number}: {строка}")
        return EXIT_FINDING
    print(
        f"#{number}: площадка приняла заголовок и тело уплотнения как есть "
        f"(тело {len(тело)} символов, подпись на месте), взвод снят"
    )
    return EXIT_OK


def main(argv: Sequence[str] | None = None) -> int:
    force_utf8_output()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--probe",
        type=int,
        required=True,
        metavar="N",
        help="номер PR с красным «PR check»: взвести, прочитать обратно, снять",
    )
    args = parser.parse_args(argv)
    if not gh_rest.token():
        print("::warning::токена нет — проба не запускалась")
        return EXIT_BROKEN
    try:
        return probe(gh_rest.repository(), args.probe)
    except gh_rest.GitHubError as exc:
        print(f"::error::площадка отказала: {exc}")
        return EXIT_BROKEN


if __name__ == "__main__":
    sys.exit(main())
