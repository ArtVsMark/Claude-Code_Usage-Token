"""Очередь мержей: один обход — не больше одного мержа (#8).

## Очередь вычисляется, а не хранится

Порядок выводится из состояния площадки на каждом прогоне: открытые PR в
`main`, по возрастанию номера — кто раньше открыт, тот раньше едет. Реестра
нет, значит нечему рассинхронизироваться, и человек может вмешаться в любой
момент, просто смержив или придержав PR руками.

## Конфликтный PR пропускается и метится

Инцидент соседнего проекта учтён заранее: конфликтный PR в голове очереди
**ронял** обход — три падения подряд, очередь стояла 14 часов, рядом ждали
четыре здоровых PR. Конфликт — штатная ситуация, а не авария: пометить и идти
дальше.

## Не больше одного мержа за прогон

В очереди одной concurrency-группы площадка держит ровно один ожидающий
прогон, и каждый новый пуш вытесняет предыдущий: тот получает `cancelled`, не
начавшись. Замер в соседнем проекте: шесть мержей подряд дали шесть отменённых
прогонов и ни одного выполненного — то есть шесть состояний `main` уехали без
единой проверки.

## Умолчание «мержить», несогласие — меткой

`merge-when-green` ставится автоматически: это **видимый признак**, а не
согласие, которое надо выпросить. Несогласие выражается стоп-меткой `hold`, и
вот почему так, а не «снять merge-when-green»: обход идемпотентен и ходит по
расписанию, а отличить «ещё не ставили» от «сняли» по состоянию PR невозможно —
оно одинаковое. Снятая метка вернулась бы на следующем проходе, и PR уехал бы
вопреки решению. Прямое снятие остаётся временной мерой до следующего обхода, и
это ожидаемое поведение, а не дефект.
"""

from __future__ import annotations

import argparse
import pathlib
import sys
from collections.abc import Sequence
from typing import Any

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import gh_rest
import pr_ready

#: Метки конвейера с описаниями. Описание обязательно: метка без него
#: выглядит как обещание, смысл которого знает только тот, кто её завёл.
PIPELINE_LABELS: tuple[tuple[str, str, str], ...] = (
    (
        pr_ready.MERGE_WHEN_GREEN,
        "0e8a16",
        "Уедет в main по зелёному. Ставится автоматически: умолчание — мержить",
    ),
    (
        pr_ready.HOLD,
        "b60205",
        "Мержить только по решению владельца. Сильнее всего остального",
    ),
    (
        pr_ready.NEEDS_REBASE,
        "d93f0b",
        "Конфликт с main: очередь такой PR пропускает, проверок на нём не создаётся",
    ),
)

EXIT_FAILED = 1
EXIT_BROKEN = 2


#: Прогон, по которому судят о состоянии общей ветки: занята ли она и красная
#: ли. Именно файл, а не «все прогоны на ветке» — обход очереди оставляет там
#: свои, и они к цвету общей ветки отношения не имеют.
CI_WORKFLOW = "ci.yml"


def main_state(repo: str, branch: str) -> tuple[bool, bool]:
    """Занят ли `main` прогоном и красный ли он.

    ## Почему здесь больше нет эталона имён

    Раньше эта функция отдавала третьим значением набор имён джобов последнего
    прогона `ci` — эталон, с которым сверялись проверки PR. Эталон ломал любой
    PR, который состав проверок **меняет**: подъём версии Python даёт другие
    имена, эталон приходит из прошлого, вердикт «джобы не созданы» становится
    вечным, и починить это в PR нельзя — чтобы эталон обновился, изменение
    должно сначала уехать в общую ветку (#46).

    Полноту набора теперь удостоверяет обязательная проверка `PR check`: она
    читает состав из дерева самого изменения и зеленеет, только когда прошли
    все прогоны на его голове.
    """
    прогоны = gh_rest.request(
        "GET",
        f"/repos/{repo}/actions/workflows/{CI_WORKFLOW}/runs",
        params={"branch": branch, "event": "push", "per_page": 20},
    )
    список = (
        (прогоны or {}).get("workflow_runs", []) if isinstance(прогоны, dict) else []
    )

    busy = any(run.get("status") != "completed" for run in список)

    завершённые = [run for run in список if run.get("status") == "completed"]
    if not завершённые:
        return busy, False

    red = завершённые[0].get("conclusion") not in {"success", "neutral", "skipped"}
    return busy, red


def snapshot_for(repo: str, number: int, *, busy: bool, red: bool) -> pr_ready.Snapshot:
    """Собрать снимок по одному PR.

    Полный объект берётся отдельным запросом: в списке PR нет ни `mergeable`,
    ни `mergeable_state`, а без них конфликт неотличим от «ещё считается».
    """
    pull = gh_rest.request("GET", f"/repos/{repo}/pulls/{number}")
    if not isinstance(pull, dict):
        raise gh_rest.GitHubError("GET", f"/pulls/{number}", 0, "ответ не объект")

    sha = ((pull.get("head") or {}) if isinstance(pull.get("head"), dict) else {}).get(
        "sha"
    )
    checks: list[dict[str, Any]] = []
    if isinstance(sha, str):
        ответ = gh_rest.request(
            "GET", f"/repos/{repo}/commits/{sha}/check-runs", params={"per_page": 100}
        )
        if isinstance(ответ, dict):
            checks = [c for c in ответ.get("check_runs", []) if isinstance(c, dict)]

    return pr_ready.Snapshot(
        pull=pull,
        checks=checks,
        main_busy=busy,
        main_red=red,
        behind_by=behind_by(repo, pull),
    )


def behind_by(repo: str, pull: dict[str, Any]) -> int:
    """На сколько коммитов ветка PR отстала от своей базы.

    Отдельным запросом, потому что `mergeable_state` этого не говорит: значение
    `behind` площадка выставляет только при включённой защите ветки с
    требованием актуальности. Без защиты отставший PR приходит как `clean` —
    и проверка «отстал ли» осталась бы гейтом, чей вход всегда зелёный.

    Ответ площадки без `behind_by` считается «не отстал»: соврать в сторону
    ожидания дешевле, чем в сторону мержа, но выдумывать отставание на пустом
    месте значило бы остановить очередь на ровном месте.
    """
    сырая_база, сырая_голова = pull.get("base"), pull.get("head")
    base: dict[str, Any] = сырая_база if isinstance(сырая_база, dict) else {}
    head: dict[str, Any] = сырая_голова if isinstance(сырая_голова, dict) else {}
    # Имя ветки, а не `base.sha`: последний — состояние базы на момент, когда
    # PR открывали, и сравнение с ним всегда дало бы ноль. Отставание считается
    # от того, где общая ветка **сейчас**.
    base_ref, head_sha = base.get("ref"), head.get("sha")
    if not isinstance(base_ref, str) or not isinstance(head_sha, str):
        return 0

    ответ = gh_rest.request("GET", f"/repos/{repo}/compare/{base_ref}...{head_sha}")
    значение = ответ.get("behind_by") if isinstance(ответ, dict) else None
    return значение if isinstance(значение, int) else 0


def ensure_labels(repo: str) -> None:
    """Завести метки конвейера, если их ещё нет.

    Метка заводится **вместе с механизмом**, который её читает, — поэтому она
    создаётся здесь, а не руками в настройках. Метка, заведённая раньше
    механизма, выглядит как обещание автоматического мержа и ровно один раз
    обманет того, кто её поставит.
    """
    for имя, цвет, описание in PIPELINE_LABELS:
        try:
            gh_rest.request(
                "POST",
                f"/repos/{repo}/labels",
                body={"name": имя, "color": цвет, "description": описание},
            )
        except gh_rest.GitHubError as exc:
            # 422 — «уже существует». Это единственный ожидаемый отказ, и
            # молча глотать все остальные значило бы прятать отсутствие прав.
            if exc.status != 422:
                raise


def set_label(repo: str, number: int, имя: str, *, поставить: bool) -> None:
    """Поставить или снять одну метку. Повторный вызов ничего не меняет."""
    if поставить:
        gh_rest.request(
            "POST", f"/repos/{repo}/issues/{number}/labels", body={"labels": [имя]}
        )
        return
    try:
        gh_rest.request("DELETE", f"/repos/{repo}/issues/{number}/labels/{имя}")
    except gh_rest.GitHubError as exc:
        if exc.status != 404:  # метки и не было — это результат, а не отказ
            raise


def reconcile_labels(
    repo: str, snapshot: pr_ready.Snapshot, verdict: pr_ready.Verdict, *, dry: bool
) -> list[str]:
    """Привести метки PR в соответствие вердикту. Возвращает, что сделано."""
    номер = snapshot.pull.get("number")
    if not isinstance(номер, int):
        return []

    метки = pr_ready.labels(snapshot.pull)
    сделано: list[str] = []

    нужен_rebase = verdict.state == pr_ready.CONFLICT
    if нужен_rebase != (pr_ready.NEEDS_REBASE in метки):
        сделано.append(
            f"{'поставить' if нужен_rebase else 'снять'} {pr_ready.NEEDS_REBASE}"
        )
        if not dry:
            set_label(repo, номер, pr_ready.NEEDS_REBASE, поставить=нужен_rebase)

    придержан = verdict.state == pr_ready.HELD
    нужен_признак = not придержан
    if нужен_признак != (pr_ready.MERGE_WHEN_GREEN in метки):
        сделано.append(
            f"{'поставить' if нужен_признак else 'снять'} {pr_ready.MERGE_WHEN_GREEN}"
        )
        if not dry:
            set_label(repo, номер, pr_ready.MERGE_WHEN_GREEN, поставить=нужен_признак)

    return сделано


#: Отказ площадки по её же правилам. Не поломка обхода: очередь прочла
#: состояние верно, а мерж не состоялся по причине на стороне площадки.
MERGE_REFUSED = 405


def merge(repo: str, number: int) -> bool:
    """Смержить PR. `False` — не поехал, и это НЕ отказ обхода.

    ## Что установлено

    Площадка отвечает `405: Required status check "PR check" is expected`, хотя
    на голове PR лежит завершённый успешный check-run ровно с этим именем.
    Отказ **не временный**: он держался полтора часа и повторился на попытке
    из другого места, не только из обхода.

    Проверено и отпало: ветка не отстала (`update-branch` отвечает «нет новых
    коммитов в базе»); ruleset не менялся с 31 августа; отменённый близнец
    `PR check` есть и у PR, которые в тот же день уехали; связь check-run с PR
    проставлена (а у уехавшего #51 её как раз не было); подвешенная сюита
    стороннего приложения есть у всех, включая уехавшие; все прогоны на голове
    завершены.

    То есть причина осталась ненайденной, и **выдавать её за известную
    нельзя**. Первая редакция этого комментария объясняла отказ гонкой —
    проверка позеленела за четыре секунды до попытки, — и это оказалось
    неверным: через полтора часа ответ тот же.

    ## Почему обход всё равно зелёный

    Обход СВОЮ работу сделал: прочитал состояние и вынес верный вердикт. Код 2
    означает «механизм не отработал», и красить им общую ветку из-за отказа
    площадки — значит приучать читать красное как шум.

    Отказ при этом не проглатывается: сообщение площадки печатается целиком
    предупреждением в каждом обходе. Если оно повторяется, PR стоит намертво, и
    расклинивает его новая голова — настоящая правка, а не пустой коммит.

    Чужие ошибки остаются отказом: 403 при отозванных правах или 500 не
    превращаются в «не в этот раз», иначе очередь молча стояла бы зелёной.
    """
    try:
        gh_rest.request(
            "PUT",
            f"/repos/{repo}/pulls/{number}/merge",
            body={"merge_method": "squash"},
        )
    except gh_rest.GitHubError as exc:
        if exc.status != MERGE_REFUSED:
            raise
        print(
            f"::warning::#{number} не поехал: площадка отказала по своим "
            f"правилам — {exc}. Обход идемпотентен и попробует снова; если "
            "это повторяется, PR стоит намертво и расклинивается новой головой"
        )
        return False
    return True


def run(repo: str, branch: str, *, dry: bool) -> int:
    """Один обход очереди."""
    открытые = gh_rest.paged(
        f"/repos/{repo}/pulls",
        params={"state": "open", "base": branch, "sort": "created", "direction": "asc"},
    )
    номера = sorted(
        pull["number"] for pull in открытые if isinstance(pull.get("number"), int)
    )
    if not номера:
        print("очередь пуста: открытых PR нет")
        return 0

    busy, red = main_state(repo, branch)
    print(
        f"состояние {branch}: {'идёт прогон' if busy else 'прогонов нет'}, "
        f"{'последний красный' if red else 'последний зелёный'}"
    )

    if not dry:
        ensure_labels(repo)

    голова: int | None = None
    for номер in номера:
        snapshot = snapshot_for(repo, номер, busy=busy, red=red)
        verdict = pr_ready.evaluate(snapshot)
        правки = reconcile_labels(repo, snapshot, verdict, dry=dry)
        хвост = f" · метки: {', '.join(правки)}" if правки else ""
        print(f"  PR #{номер}: {verdict.state} — {'; '.join(verdict.reasons)}{хвост}")

        # Конфликтный и придержанный не держат очередь: их пропускают.
        if verdict.state in {pr_ready.CONFLICT, pr_ready.HELD, pr_ready.BLOCKED}:
            continue
        if голова is None:
            голова = номер
            if verdict.state == pr_ready.STALE:
                print(f"  → обновляю голову очереди #{номер} из {branch}")
                if not dry:
                    gh_rest.request("PUT", f"/repos/{repo}/pulls/{номер}/update-branch")
            elif verdict.ready:
                print(f"  → мержу #{номер}")
                if not dry and not merge(repo, номер):
                    # Не поехал — значит поедет следующим обходом. Пробовать
                    # второй PR в этом же проходе нельзя: правило «не больше
                    # одного мержа» про вытеснение прогона на main, и оно не
                    # перестаёт действовать оттого, что первый не уехал.
                    break
                # Больше одного мержа за прогон не делаем: следующий вытеснил
                # бы ожидающий прогон на main, не дав ему начаться.
                break

    if голова is None:
        print("двигать нечего: у очереди нет головы, которая могла бы поехать")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    парсер = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    парсер.add_argument("--branch", default="main", help="общая ветка")
    парсер.add_argument(
        "--dry-run",
        action="store_true",
        help="показать, что было бы сделано, ничего не меняя",
    )
    аргументы = парсер.parse_args(list(argv) if argv is not None else None)

    if not gh_rest.token():
        print(
            "::warning::токен не задан — очередь не двигается. Нужен секрет с "
            "правами contents:write и pull-requests:write"
        )
        return 0

    try:
        repo = gh_rest.repository()
        return run(repo, аргументы.branch, dry=аргументы.dry_run)
    except gh_rest.GitHubError as exc:
        print(f"обход не отработал: {exc}", file=sys.stderr)
        return EXIT_BROKEN


if __name__ == "__main__":
    raise SystemExit(main())
