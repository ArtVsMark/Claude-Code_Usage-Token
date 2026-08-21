# claude-code-usage

**How much of your Claude Code limits is actually left — as a number, not a traffic light.**

> 🇷🇺 [Русская версия](README.md) · Unofficial tool, not affiliated with Anthropic.

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

## Status

Early development. Specification: [`docs/spec.md`](docs/spec.md) (Russian).

## License

MIT — see [LICENSE](LICENSE).
