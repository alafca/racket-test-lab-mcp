"""Read-only access to the lab database: connection, input validation and queries.

Every query is parameterized. The only values ever interpolated into SQL are column
names taken from the METRICS whitelist below, never raw user input.
"""

import os
import re
import sqlite3
import statistics
from contextlib import contextmanager
from datetime import date
from pathlib import Path

# test_type -> the column holding that test's measurement
METRICS = {
    "power": "ball_exit_speed_mph",
    "spin": "spin_rpm",
    "durability": "hits_to_failure",
    "swingweight": "swingweight",
}
METRIC_COLUMNS = set(METRICS.values())
STATUSES = {"prototype", "approved", "rejected"}
# Used with fullmatch: `$` alone would also accept a trailing newline, and `\d` any Unicode digit.
RACKET_ID_RE = re.compile(r"[A-Z]{2}-[0-9]{2,3}-[A-Z]")
MAX_LIMIT = 200
MIN_CONFIDENT_N = 5

SCHEMA_DOC = """Racket Test Lab schema (SQLite, read-only)

rackets: one row per tennis racket prototype
  id               TEXT    primary key, format XX-NN(N)-X (e.g. RK-100-A; the number = head size)
  model_name       TEXT    internal model name
  frame_material   TEXT    'graphite' | 'graphite-basalt' | 'graphite-kevlar'
  head_size_sq_in  INTEGER head size in square inches (95, 98, 100, 105)
  beam_mm          REAL    frame beam width in mm (21-27; wider = stiffer, more power)
  string_pattern   TEXT    '16x19' (open, more spin) | '18x20' (dense, more control)
  weight_g         REAL    strung weight in grams
  status           TEXT    'prototype' | 'approved' | 'rejected'
  created_at       DATE    ISO date

test_runs: one row per lab test; exactly one measurement column is set, matching test_type
  id                   INTEGER primary key
  racket_id            TEXT    -> rackets.id
  test_type            TEXT    'power' | 'spin' | 'durability' | 'swingweight'
  run_date             DATE    ISO date
  technician           TEXT
  ball_exit_speed_mph  REAL    power tests, ball speed off the stringbed in mph (spec >= 63)
  spin_rpm             REAL    spin tests, topspin rpm on a standard brushing stroke (spec >= 2100)
  hits_to_failure      INTEGER durability tests, machine impacts until frame failure (spec >= 40000)
  swingweight          REAL    kg*cm^2 (spec 300-330; a trade-off, not higher-is-better)
  passed               BOOLEAN whether the run met spec
  notes                TEXT    technician notes on failures and follow-up retests
"""


class LabError(ValueError):
    """Bad input or unknown entity. The message is safe to show to the caller."""


@contextmanager
def connect(path: str | Path | None = None):
    """Open the database read-only. mode=ro makes SQLite itself reject any write."""
    path = Path(path or os.environ.get("RACKET_LAB_DB") or "data/lab.db").absolute()
    if not path.is_file():
        raise LabError(f"Database not found at {path}. Run: uv run python -m racket_lab.seed")
    conn = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


# --- validation ---------------------------------------------------------------

def _check_choice(name: str, value: str, allowed) -> str:
    if value not in allowed:
        raise LabError(f"Invalid {name} {value!r}. Must be one of: {', '.join(sorted(allowed))}")
    return value


def _check_date(value: str) -> str:
    try:
        return date.fromisoformat(value).isoformat()
    except (TypeError, ValueError):
        raise LabError(f"Invalid date {value!r}. Use ISO format YYYY-MM-DD.") from None


def _check_racket(conn: sqlite3.Connection, racket_id: str) -> sqlite3.Row:
    """Validate the id's format, then make sure the racket exists."""
    if not isinstance(racket_id, str) or not RACKET_ID_RE.fullmatch(racket_id):
        raise LabError(f"Invalid racket_id {racket_id!r}. Expected format like 'RK-100-A'.")
    row = conn.execute("SELECT * FROM rackets WHERE id = ?", (racket_id,)).fetchone()
    if row is None:
        raise LabError(f"Unknown racket {racket_id!r}. Use list_rackets to see valid ids.")
    return row


def _run_dict(row: sqlite3.Row) -> dict:
    """Row -> dict, dropping empty measurement columns to keep responses compact."""
    run = {k: row[k] for k in row.keys() if row[k] is not None}
    run["passed"] = bool(run["passed"])
    return run


# --- queries -----------------------------------------------------------------

def list_rackets(conn, status: str | None = None, head_size_sq_in: int | None = None) -> dict:
    sql, params = "SELECT * FROM rackets WHERE 1 = 1", []
    if status is not None:
        sql += " AND status = ?"
        params.append(_check_choice("status", status, STATUSES))
    if head_size_sq_in is not None:
        sql += " AND head_size_sq_in = ?"
        params.append(head_size_sq_in)
    rackets = [dict(r) for r in conn.execute(sql + " ORDER BY id", params)]
    return {"count": len(rackets), "rackets": rackets}


def get_racket(conn, racket_id: str) -> dict:
    racket = dict(_check_racket(conn, racket_id))
    summary = {}
    for test_type, column in METRICS.items():
        rows = conn.execute(
            f"SELECT {column} AS value, passed FROM test_runs"
            f" WHERE racket_id = ? AND test_type = ? AND {column} IS NOT NULL",
            (racket_id, test_type),
        ).fetchall()
        if rows:
            values = [r["value"] for r in rows]
            summary[test_type] = {
                "metric": column,
                "count": len(rows),
                "mean": round(statistics.mean(values), 2),
                "min": min(values),
                "max": max(values),
                "pass_rate": round(sum(r["passed"] for r in rows) / len(rows), 2),
            }
    return {"racket": racket, "test_summary": summary}


def get_test_runs(conn, racket_id: str, test_type: str | None = None, limit: int = 50) -> dict:
    _check_racket(conn, racket_id)
    limit = max(1, min(int(limit), MAX_LIMIT))
    sql, params = "SELECT * FROM test_runs WHERE racket_id = ?", [racket_id]
    if test_type is not None:
        sql += " AND test_type = ?"
        params.append(_check_choice("test_type", test_type, METRICS))
    rows = conn.execute(sql + " ORDER BY run_date DESC, id DESC LIMIT ?", [*params, limit])
    runs = [_run_dict(r) for r in rows]
    return {"racket_id": racket_id, "count": len(runs), "limit": limit, "runs": runs}


def compare_rackets(conn, racket_ids: list[str], metric: str) -> dict:
    column = _check_choice("metric", metric, METRIC_COLUMNS)
    if not racket_ids:
        raise LabError("racket_ids must contain at least one racket id.")
    results = []
    for pid in dict.fromkeys(racket_ids):  # de-duplicate, keep order
        _check_racket(conn, pid)
        values = [r[0] for r in conn.execute(
            f"SELECT {column} FROM test_runs WHERE racket_id = ? AND {column} IS NOT NULL", (pid,)
        )]
        results.append({"racket_id": pid, **_stats(values)})

    notes, best = [], None
    low_n = [r["racket_id"] for r in results if r["n"] < MIN_CONFIDENT_N]
    if low_n:
        notes.append(f"Small samples (n < {MIN_CONFIDENT_N}) for {', '.join(low_n)}; treat differences as tentative.")
    measured = [r for r in results if r["n"]]
    if metric == "swingweight":
        # A feel trade-off (light = fast hands, heavy = stability), so there is no "best".
        notes.append("Swingweight is a trade-off, not higher-is-better; no single best racket.")
    elif measured:
        best = max(measured, key=lambda r: r["mean"])["racket_id"]
    return {"metric": metric, "results": results, "best": best, "notes": notes}


def _stats(values: list[float]) -> dict:
    return {
        "n": len(values),
        "mean": round(statistics.mean(values), 2) if values else None,
        "std": round(statistics.stdev(values), 2) if len(values) > 1 else None,  # sample std needs n >= 2
    }


def find_failures(conn, since: str | None = None) -> dict:
    sql = ("SELECT r.*, p.model_name FROM test_runs r JOIN rackets p ON p.id = r.racket_id"
           " WHERE r.passed = 0")
    params = []
    if since is not None:
        sql += " AND r.run_date >= ?"
        params.append(_check_date(since))
    rows = conn.execute(sql + " ORDER BY r.run_date DESC, r.id DESC LIMIT ?", [*params, MAX_LIMIT])
    failures = [_run_dict(r) for r in rows]
    # count == limit means the list may be truncated
    return {"since": since, "count": len(failures), "limit": MAX_LIMIT, "failures": failures}
