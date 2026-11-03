"""MCP server exposing read-only tennis racket R&D test data. Tool definitions only; logic lives in db.py.

Run: uv run python -m racket_lab.server   (stdio transport)
"""

import functools
import logging
import time
from datetime import date, timedelta

from mcp.server.fastmcp import FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.types import ToolAnnotations

from racket_lab import db

# stdout carries the MCP protocol over stdio, so all logging must go to stderr.
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")  # stderr by default
log = logging.getLogger("racket_lab")

mcp = FastMCP("racket-test-lab")
READ_ONLY = ToolAnnotations(readOnlyHint=True)


def logged(fn):
    """Log each call (name, args, duration) and turn validation errors into clean MCP tool errors."""
    @functools.wraps(fn)
    def wrapper(**kwargs):
        start = time.perf_counter()
        try:
            return fn(**kwargs)
        except db.LabError as e:
            # ToolError -> result with isError=true and just this message, no traceback.
            raise ToolError(str(e)) from None
        finally:
            log.info("tool=%s args=%s duration_ms=%.1f", fn.__name__, kwargs, (time.perf_counter() - start) * 1000)
    return wrapper


@mcp.tool(annotations=READ_ONLY)
@logged
def list_rackets(status: str | None = None, head_size_sq_in: int | None = None) -> dict:
    """List tennis racket prototypes, optionally filtered.

    Args:
        status: 'prototype', 'approved' or 'rejected'.
        head_size_sq_in: head size in square inches, e.g. 95, 98, 100 or 105.
    """
    with db.connect() as conn:
        return db.list_rackets(conn, status, head_size_sq_in)


@mcp.tool(annotations=READ_ONLY)
@logged
def get_racket(racket_id: str) -> dict:
    """Get one racket's specs plus a per-test-type summary (count, mean, min, max, pass rate).

    Args:
        racket_id: racket id like 'RK-100-A'.
    """
    with db.connect() as conn:
        return db.get_racket(conn, racket_id)


@mcp.tool(annotations=READ_ONLY)
@logged
def get_test_runs(racket_id: str, test_type: str | None = None, limit: int = 50) -> dict:
    """Get raw test runs for one racket, newest first.

    Args:
        racket_id: racket id like 'RK-100-A'.
        test_type: optional filter: 'power', 'spin', 'durability' or 'swingweight'.
        limit: max runs to return (default 50, capped at 200).
    """
    with db.connect() as conn:
        return db.get_test_runs(conn, racket_id, test_type, limit)


@mcp.tool(annotations=READ_ONLY)
@logged
def compare_rackets(racket_ids: list[str], metric: str) -> dict:
    """Compare rackets on one metric: mean, std and sample size (n) per racket, plus the best one.

    Notes flag rackets with n < 5, where differences should be treated as tentative.

    Args:
        racket_ids: racket ids to compare, e.g. ['RK-100-A', 'RK-98-B'].
        metric: 'ball_exit_speed_mph', 'spin_rpm', 'hits_to_failure' or 'swingweight'.
            Higher is better except swingweight, which is a trade-off with no single best.
    """
    with db.connect() as conn:
        return db.compare_rackets(conn, racket_ids, metric)


@mcp.tool(annotations=READ_ONLY)
@logged
def find_failures(since: str | None = None) -> dict:
    """List failed test runs (newest first) with technician notes, for quality review.

    Args:
        since: optional ISO date (YYYY-MM-DD); only failures on or after this date.
    """
    with db.connect() as conn:
        return db.find_failures(conn, since)


@mcp.resource("lab://schema")
def schema() -> str:
    """Tables, columns, units and pass specs of the lab database."""
    return db.SCHEMA_DOC


@mcp.prompt()
def weekly_rd_summary() -> str:
    """Summarize the last 7 days of R&D testing."""
    since = (date.today() - timedelta(days=7)).isoformat()
    return (
        f"Write a weekly R&D summary of racket testing since {since}.\n"
        "1. Read the lab://schema resource if you need column meanings or specs.\n"
        f"2. Call find_failures(since='{since}') and list_rackets(status='prototype').\n"
        "3. For prototypes, use get_test_runs to find runs from this week and get_racket for context.\n"
        "Then report: key results (best power/spin/durability numbers and how they compare), "
        "failures with their likely cause from the notes, suspicious outliers, "
        "whether follow-up retests (see run notes) resolved earlier issues, "
        "and 3 recommended next tests, flagging where sample sizes are too small to be confident."
    )


if __name__ == "__main__":
    mcp.run()
