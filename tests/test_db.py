import sqlite3

import pytest

from racket_lab import db
from racket_lab.db import LabError


def test_list_rackets_filters(conn):
    assert db.list_rackets(conn)["count"] == 12
    approved = db.list_rackets(conn, status="approved")["rackets"]
    assert approved and all(p["status"] == "approved" for p in approved)
    thick = db.list_rackets(conn, head_size_sq_in=100)["rackets"]
    assert {p["id"] for p in thick} == {"RK-100-A", "RK-100-B", "RK-100-C", "RK-100-D"}


def test_list_rackets_rejects_bad_status(conn):
    with pytest.raises(LabError, match="Invalid status"):
        db.list_rackets(conn, status="shipped")


def test_get_racket_summary(conn):
    result = db.get_racket(conn, "RK-100-A")
    assert result["racket"]["head_size_sq_in"] == 100
    power = result["test_summary"]["power"]
    assert power["min"] <= power["mean"] <= power["max"]
    assert 0 <= power["pass_rate"] <= 1


def test_unknown_racket_is_clean_error(conn):
    with pytest.raises(LabError, match="Unknown racket"):
        db.get_racket(conn, "ZZ-99-Z")


def test_get_test_runs_newest_first_and_filtered(conn):
    runs = db.get_test_runs(conn, "RK-100-A", test_type="spin")["runs"]
    assert runs and all(r["test_type"] == "spin" for r in runs)
    dates = [r["run_date"] for r in runs]
    assert dates == sorted(dates, reverse=True)
    assert "hits_to_failure" not in runs[0]  # empty columns dropped


def test_get_test_runs_limit_capped(conn):
    assert db.get_test_runs(conn, "RK-100-A", limit=10_000)["limit"] == db.MAX_LIMIT
    assert db.get_test_runs(conn, "RK-100-A", limit=2)["count"] == 2


def test_get_test_runs_rejects_bad_test_type(conn):
    with pytest.raises(LabError, match="Invalid test_type"):
        db.get_test_runs(conn, "RK-100-A", test_type="vibes")


def test_compare_rackets(conn):
    result = db.compare_rackets(conn, ["RK-100-A", "RK-105-A", "RK-100-A"], "spin_rpm")
    assert [r["racket_id"] for r in result["results"]] == ["RK-100-A", "RK-105-A"]
    best = max(result["results"], key=lambda r: r["mean"])
    assert result["best"] == best["racket_id"]


def test_compare_flags_small_samples(conn):
    result = db.compare_rackets(conn, ["RK-100-A", "RK-100-D"], "hits_to_failure")
    assert any("RK-100-D" in note for note in result["notes"])


def test_compare_swingweight_has_no_best(conn):
    assert db.compare_rackets(conn, ["RK-100-A", "RK-105-A"], "swingweight")["best"] is None


def test_compare_rejects_invalid_metric(conn):
    with pytest.raises(LabError, match="Invalid metric"):
        db.compare_rackets(conn, ["RK-100-A"], "weight_g; DROP TABLE rackets")


def test_find_failures(conn):
    failures = db.find_failures(conn)["failures"]
    assert failures and all(f["passed"] is False for f in failures)
    assert all("notes" in f for f in failures)
    recent = db.find_failures(conn, since="2100-01-01")
    assert recent["count"] == 0


def test_find_failures_rejects_bad_date(conn):
    with pytest.raises(LabError, match="Invalid date"):
        db.find_failures(conn, since="last tuesday")


@pytest.mark.parametrize("bad_id", ["RK-100-A\n", "RK-\u0661\u0660\u0660-A", "rk-100-a", "RK-1000-A"])
def test_malformed_racket_ids_rejected(conn, bad_id):
    with pytest.raises(LabError, match="Invalid racket_id"):
        db.get_racket(conn, bad_id)


def test_sql_injection_in_racket_id_rejected(conn):
    with pytest.raises(LabError, match="Invalid racket_id"):
        db.get_racket(conn, "RK-100-A'; DROP TABLE rackets;--")
    assert db.list_rackets(conn)["count"] == 12


def test_connection_is_read_only(conn):
    with pytest.raises(sqlite3.OperationalError, match="readonly"):
        conn.execute("DELETE FROM rackets")


def test_missing_database_is_clean_error(tmp_path):
    with pytest.raises(LabError, match="Database not found"):
        with db.connect(tmp_path / "nope.db"):
            pass


def test_compare_requires_rackets(conn):
    with pytest.raises(LabError, match="at least one"):
        db.compare_rackets(conn, [], "spin_rpm")


def test_stats_without_values():
    assert db._stats([]) == {"n": 0, "mean": None, "std": None}


def test_find_failures_since_is_inclusive(conn):
    oldest = db.find_failures(conn)["failures"][-1]
    result = db.find_failures(conn, since=oldest["run_date"])["failures"]
    assert oldest["id"] in {f["id"] for f in result}
    assert all(f["run_date"] >= oldest["run_date"] for f in result)


def test_seed_contains_outliers(conn):
    assert conn.execute(
        "SELECT 1 FROM test_runs WHERE racket_id = 'RK-100-A' AND ball_exit_speed_mph = 79.6").fetchone()
    assert conn.execute(
        "SELECT 1 FROM test_runs WHERE racket_id = 'RK-98-A' AND hits_to_failure = 9800 AND passed = 0").fetchone()


def test_approved_rackets_meet_spec(conn):
    """Seed consistency: an approved racket should pass nearly all of its runs."""
    for racket in db.list_rackets(conn, status="approved")["rackets"]:
        runs = db.get_test_runs(conn, racket["id"], limit=200)["runs"]
        pass_rate = sum(r["passed"] for r in runs) / len(runs)
        assert pass_rate >= 0.9, (racket["id"], pass_rate)


def test_follow_ups_resolve_open_issues(conn):
    # The radar-misread outlier was retested and the newest power runs are back in range.
    latest_power = db.get_test_runs(conn, "RK-100-A", "power", limit=3)["runs"]
    assert all("retest" in r["notes"] and r["ball_exit_speed_mph"] < 75 for r in latest_power)
    # The bad-layup durability failure was retested on a new batch and passed.
    [latest_durability] = db.get_test_runs(conn, "RK-98-A", "durability", limit=1)["runs"]
    assert latest_durability["passed"] and "batch B2" in latest_durability["notes"]
    # The newest racket now has a swingweight measurement.
    assert "swingweight" in db.get_racket(conn, "RK-100-D")["test_summary"]
