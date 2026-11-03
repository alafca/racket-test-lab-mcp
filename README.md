# Racket Test Lab MCP

[![tests](https://github.com/alafca/racket-test-lab-mcp/actions/workflows/tests.yml/badge.svg)](https://github.com/alafca/racket-test-lab-mcp/actions/workflows/tests.yml)

A small [Model Context Protocol](https://modelcontextprotocol.io) server that gives an AI assistant (Claude Desktop, Claude Code) **secure, read-only** access to R&D test results for tennis racket prototypes. Engineers can ask questions in plain language, like "which 100 sq in frame has the best spin without losing durability?", and the assistant answers from lab data using a small set of well-defined tools instead of guessing or running arbitrary SQL.

The data is mock data: 12 invented prototypes and about 160 test runs over the last six months, covering power, spin, durability and swingweight. It's generated with simple physical relationships:
- A wider beam or bigger head gives more power.
- An open 16x19 string pattern gives more spin.
- Basalt and kevlar layups make the frame more durable.
- Very wide, stiff beams crack sooner.

The same pattern applies to any equipment lab: swap the schema and keep the tools.

## What it exposes

| Kind | Name | Purpose |
|---|---|---|
| Tool | `list_rackets(status?, head_size_sq_in?)` | Rackets matching optional filters |
| Tool | `get_racket(racket_id)` | Specs plus count/mean/min/max/pass rate per test type |
| Tool | `get_test_runs(racket_id, test_type?, limit=50)` | Raw runs, newest first (limit capped at 200) |
| Tool | `compare_rackets(racket_ids, metric)` | Mean, std, n per racket; best racket; small-sample warnings |
| Tool | `find_failures(since?)` | Failed runs with technician notes |
| Resource | `lab://schema` | Tables, columns, units and pass specs |
| Prompt | `weekly_rd_summary` | Summarize the last 7 days: results, failures, retests, next tests |

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Python 3.11+.

```bash
uv sync                               # install dependencies
uv run python -m racket_lab.seed      # create data/lab.db with mock data
uv run pytest                         # run the tests
```

The seed is deterministic (fixed random seed). Run dates are relative to the day you seed, so there is always recent data for the weekly summary. This week's runs are follow-ups to earlier issues, such as a retest after a suspected radar misread and a durability retest on a new layup batch, so the assistant has something to reconcile.

The database path defaults to `data/lab.db`. Set the `RACKET_LAB_DB` environment variable to use a different path.

## Try it with MCP Inspector

```bash
uv run mcp dev src/racket_lab/server.py
```

This opens the Inspector in your browser. From there you can list the tools, resource and prompt, and call each one.

## Connect to Claude Desktop

Add this to `claude_desktop_config.json` (Settings → Developer → Edit Config), using the absolute path to this repo:

```json
{
  "mcpServers": {
    "racket-test-lab": {
      "command": "uv",
      "args": ["--directory", "/absolute/path/to/racket-test-lab-mcp", "run", "python", "-m", "racket_lab.server"]
    }
  }
}
```

`--directory` makes uv run from the repo, so the default `data/lab.db` path resolves. Restart Claude Desktop afterwards.

## Connect to Claude Code

From the repo directory:

```bash
claude mcp add racket-test-lab -- uv --directory "$(pwd)" run python -m racket_lab.server
```

This registers the server for this project only. Add `-s user` to make it available everywhere. Then:
1. Start a new Claude Code session.
2. Check that `/mcp` lists `racket-test-lab`.
3. Run the weekly prompt with `/mcp__racket-test-lab__weekly_rd_summary`.

## Example questions

- "Which 100 sq in prototype has the best spin without sacrificing durability?"
- "Compare RK-100-A and RK-98-B on ball exit speed. Is the difference meaningful?"
- "What failed in durability testing and why?"
- "Did the RK-100-A power retest confirm the radar misread?"
- "Why were RK-95-B and RK-105-B rejected?"
- "Give me the weekly R&D summary." (uses the `weekly_rd_summary` prompt)

### Example session (Claude Code, seeded data)

> **Compare RK-100-A and RK-98-B on ball exit speed. Is the difference meaningful?**

The assistant calls `compare_rackets(["RK-100-A", "RK-98-B"], "ball_exit_speed_mph")`:

```json
{"results": [{"racket_id": "RK-100-A", "n": 9, "mean": 70.21, "std": 3.67},
             {"racket_id": "RK-98-B", "n": 4, "mean": 67.72, "std": 0.7}],
 "best": "RK-100-A",
 "notes": ["Small samples (n < 5) for RK-98-B; treat differences as tentative."]}
```

RK-100-A's spread is suspiciously wide, so the assistant follows up with `get_test_runs("RK-100-A", "power")` and finds:
- One 79.6 mph run noted *"suspected radar misread, retest requested"*.
- Three newer runs at 68.4–70.8 mph, noted *"retest after radar recalibration"*.

Its answer: the retest confirms the misread. Without that run, RK-100-A averages about 69 mph, which is still roughly 1.3 mph above RK-98-B. That fits its bigger head and wider beam. With n = 4 for RK-98-B, it's a likely difference, not a proven one.

> **Get racket `RK-100-A'; DROP TABLE rackets;--`**

```
Error executing tool get_racket: Invalid racket_id "RK-100-A'; DROP TABLE rackets;--". Expected format like 'RK-100-A'.
```

## Layout

```
src/racket_lab/
  server.py   # MCP tool, resource and prompt definitions only
  db.py       # read-only connection, input validation, queries
  seed.py     # creates and fills the mock database
tests/        # pytest: queries, validation, injection, read-only, seed consistency, MCP registration
```

## Design decisions

- **Read-only at two levels.** SQLite is opened with `mode=ro`, so the database engine itself refuses writes. The tools also carry the MCP `readOnlyHint` annotation.
- **No raw SQL tool.** The assistant can only ask the questions the tools allow. That keeps the attack surface small and the answers consistent.
- **Parameterized queries only.** User input is never formatted into SQL. The only identifiers put into SQL strings are column names from a fixed whitelist.
- **Validated and whitelisted inputs.**
  - `metric`, `test_type` and `status` must come from fixed sets.
  - `racket_id` must fully match `[A-Z]{2}-[0-9]{2,3}-[A-Z]`.
  - Dates must be ISO format.
  - Bad input returns an MCP tool error (`isError: true`) with a message the model can act on, not a stack trace.
- **Honest statistics.** `compare_rackets` reports n and std alongside the mean and flags samples below 5, so the assistant doesn't overclaim from three runs. Swingweight is treated as a trade-off with no single "best".
- **Consistent mock data.** Tests check that approved rackets pass at least 90% of their runs, and that this week's follow-up runs actually resolve the open issues.
- **Logging to stderr.** stdout carries the stdio MCP protocol, so every tool call (name, args, duration) is logged to stderr.
- **Pinned to MCP SDK v1 (`mcp<2`).** v2 renamed `FastMCP` to `MCPServer`, so migrating is a small rename.

### What I'd add for production

- Authentication and per-user authorization, using the HTTP (streamable) transport instead of local stdio.
- A connection to the real LIMS or test database (read replica or a read-only service account) instead of SQLite.
- A persistent audit log of who asked what, kept separate from the debug logs.
- A React dashboard on the same query layer, so engineers can see the charts behind the assistant's answers.
