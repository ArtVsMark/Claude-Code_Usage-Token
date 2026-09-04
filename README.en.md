# Claude-Code_Usage-Token

[![Version](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2FArtVsMark%2FClaude-Code_Usage-Token%2Fbadges%2F.github%2Fbadges%2Fversion.json&cacheSeconds=300)](pyproject.toml)

**How much of your Claude Code limits is actually left — as a number, not a traffic light.**

> 🇷🇺 [Русская версия](README.md) · Unofficial tool, not affiliated with Anthropic.

> **What the showcase does not answer, and why.** The question set is one and
> the same across projects ([`.rules/showcase.json`](.rules/showcase.json)), and
> a gap in it is named rather than omitted: of the ten questions **one** is
> answered by a live number, the other nine are named with a reason. There is no
> **CI** badge — the platform reports run state itself, and a second source of
> the same thing drifts from the first silently. There is no **release**
> badge — the platform lists releases itself, on the Releases tab. There is no
> **PyPI** badge — nothing is published there before `1.0`, and that is the
> owner's decision rather than a gap. There is no **coverage** badge — coverage
> is not measured here, and a number filled in by hand would freeze on the very
> next change.
>
> **No badge does not mean no number.** Facts about how the project is built —
> how many tests and test modules, which Python versions and platforms the
> checks run on, how many checks a change creates, what holds each catalogue
> rule — the project publishes about itself, machine-readable:
> `.github/badges/facts.json` on the `badges` branch, built by
> `scripts/facts.py`. That is an answer to a neighbouring showcase, not a
> picture for a reader, and the questions differ. A section that cannot be
> counted **exactly** does not go into the file at all: a missing key means «not
> measured», whereas a zero in its place would read as a measured answer.
>
> The gap is named because a missing badge and a frozen badge look
> **identical** from the outside. This is held by the `scripts/preflight.py`
> gate: a question without an answer, and a badge that has drifted from the
> tree, are refusals rather than warnings. The same gate keeps a facts section
> from disappearing silently: if its source is in the tree and no number comes
> out of it, that is a refusal.

## The problem

Claude Code reports limit state in three steps:

```
allowed  →  allowed_warning  →  rejected
```

You cannot plan against that. "Warning" covers both half your budget and the
last few percent — and there is no way to tell which: no number, no share, not
in any interface available to you.

The practical consequence is familiar to anyone running agent sessions: whether
to start a large task now or wait for the reset is a guess.

## The idea

The remainder is not queried. It is **measured**.

Next to the traffic light sits the actual consumption of every session — cache
read, output tokens, notional cost. The tool samples those figures, accumulates
them, and computes usage per window: five-hour and seven-day.

Then it **calibrates itself**: it records what cumulative usage corresponded to
the switch from `allowed` to `warning`, and later to `rejected`. After two or
three weeks a scale of your own emerges — and the remainder starts being
reported as a percentage, with no access to internal limits whatsoever.

## How storage works

No server, no database. Samples are appended as **JSONL** lines into git — a
private repository alongside this one.

The reason is architectural: only a session can take the sample (the data lives
in the session registry), and a cloud session is ephemeral — its filesystem
disappears with the container. Storage therefore has to be external, and git
beats a server here: it is distributed, versioned, and reachable from any
session. Append-only lines merge without conflicts even when several sessions
write at once.

A server would add nothing but a point of failure.

## Documents

Written in Russian; the short English summary of each is in
[`CONTRIBUTING.md`](CONTRIBUTING.md) and [`SECURITY.md`](SECURITY.md), both of
which open with an English note.

| | |
|---|---|
| [`docs/spec.md`](docs/spec.md) | what is measured, how the remainder follows from it, what the project does not promise |
| [`docs/storage-setup.md`](docs/storage-setup.md) | preparing the private storage before the first sample |
| [`docs/roles.md`](docs/roles.md) | roles: when each engages and what it may block |
| [`docs/labels.md`](docs/labels.md) | label nomenclature |
| [`CONTRIBUTING.md`](CONTRIBUTING.md) | invariants, gates, labels, language |
| [`SECURITY.md`](SECURITY.md) | what is collected, where it goes, how to report a leak |
| [`HISTORY.md`](HISTORY.md) | why the project exists and which decisions are settled |
| [`.rules/bindings.json`](.rules/bindings.json) | this project's answer for every rule of the shared catalogue |

**The project's rules come from a shared catalogue.** The body of rules worked
to here lives in
[Engineering-Incidents-Playbook](https://github.com/ArtVsMark/Engineering-Incidents-Playbook),
where every entry carries its incident and its limits. How many there are is
said by the catalogue itself, not by this line: a number copied here freezes
silently (this one said "162" while fourteen more rules arrived). This
project's answer — for
every rule, not just the ones somebody got to — is in
[`.rules/bindings.json`](.rules/bindings.json): accepted and what holds it ·
rejected and why · no subject here · not yet reviewed. Rules born here are
proposed back to the catalogue in
[`.rules/proposals.json`](.rules/proposals.json).

## Status

Early development. The **first command is built — `sample`**: it takes a
measurement and appends it to the private store. Report and calibrate are not
there yet, and they refuse out loud rather than returning emptiness.

**Half of it has now been run on real material.** Transcript collection was
exercised against a live transcript on 2026-09-03 — 3833 lines, 650 responses —
and the very first run found a defect: the output said nothing about 572
discarded duplicates, that is 47% of the lines carrying usage. The registry half
and the write to storage have **never been run**: not a single measurement has
been written, and the most expensive class of defect here is writing to
someone's private repository, committing and pushing.

Order of work: epics
[#1](https://github.com/ArtVsMark/Claude-Code_Usage-Token/issues/1) (sample → report
→ calibrate) and
[#3](https://github.com/ArtVsMark/Claude-Code_Usage-Token/issues/3) (pre-launch
infrastructure).

The tool will not report a percentage until it has collected calibration
points; until then it says "the scale is not calibrated". That is the intended
behaviour, not a missing feature.

Only a **window** can take a measurement: the session registry lives in MCP,
and a Python process cannot reach it by construction. So the window dumps the
registry response to a file and the tool parses it — details in
[`docs/spec.md`](docs/spec.md), store preparation in
[`docs/storage-setup.md`](docs/storage-setup.md).

## License

MIT — see [LICENSE](LICENSE).
