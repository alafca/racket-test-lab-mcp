"""Create the lab database and fill it with deterministic, physically plausible mock data.

Run with: uv run python -m racket_lab.seed
"""

import os
import random
import sqlite3
from datetime import date, timedelta
from pathlib import Path

SCHEMA = """
CREATE TABLE rackets (
    id               TEXT PRIMARY KEY,
    model_name       TEXT NOT NULL,
    frame_material   TEXT NOT NULL CHECK (frame_material IN ('graphite', 'graphite-basalt', 'graphite-kevlar')),
    head_size_sq_in  INTEGER NOT NULL,
    beam_mm          REAL NOT NULL,
    string_pattern   TEXT NOT NULL CHECK (string_pattern IN ('16x19', '18x20')),
    weight_g         REAL NOT NULL,
    status           TEXT NOT NULL CHECK (status IN ('prototype', 'approved', 'rejected')),
    created_at       DATE NOT NULL
);
CREATE TABLE test_runs (
    id                  INTEGER PRIMARY KEY,
    racket_id           TEXT NOT NULL REFERENCES rackets(id),
    test_type           TEXT NOT NULL CHECK (test_type IN ('power', 'spin', 'durability', 'swingweight')),
    run_date            DATE NOT NULL,
    technician          TEXT NOT NULL,
    ball_exit_speed_mph REAL,
    spin_rpm            REAL,
    hits_to_failure     INTEGER,
    swingweight         REAL,
    passed              BOOLEAN NOT NULL,
    notes               TEXT
);
"""

# (id, model_name, frame, head_size_sq_in, beam_mm, string_pattern, weight_g, status, created_days_ago)
RACKETS = [
    ("RK-95-A", "Vector 95A", "graphite", 95, 21, "16x19", 315, "approved", 170),
    ("RK-95-B", "Vector 95B", "graphite-kevlar", 95, 21, "18x20", 318, "rejected", 165),
    ("RK-98-A", "Vector 98A", "graphite-basalt", 98, 23, "16x19", 305, "approved", 150),
    ("RK-98-B", "Torque 98B", "graphite", 98, 23, "16x19", 300, "prototype", 120),
    ("RK-98-C", "Torque 98C", "graphite-kevlar", 98, 23, "18x20", 310, "prototype", 100),
    ("RK-100-A", "Surge 100A", "graphite-basalt", 100, 25, "16x19", 300, "approved", 175),
    ("RK-100-B", "Surge 100B", "graphite-kevlar", 100, 25, "16x19", 305, "prototype", 110),
    ("RK-100-C", "Surge 100C", "graphite", 100, 25, "18x20", 295, "prototype", 90),
    ("RK-100-D", "Surge 100D", "graphite", 100, 27, "16x19", 290, "prototype", 20),
    ("RK-105-A", "Surge 105A", "graphite-basalt", 105, 27, "16x19", 295, "approved", 160),
    ("RK-105-B", "Surge 105B", "graphite", 105, 27, "18x20", 290, "rejected", 140),
    ("RK-105-C", "Torque 105C", "graphite-kevlar", 105, 25, "16x19", 295, "prototype", 60),
]

TECHNICIANS = ["Maya", "Tomas", "Priya", "Jordan", "Lena"]

# Power and spin are cheap, non-destructive tests, so they run twice as often as
# destructive durability tests or swingweight checks.
TEST_CYCLE = ["power", "spin", "power", "spin", "durability", "swingweight"]

# Material effects: wider beam and bigger head -> more power; open 16x19 pattern -> more spin;
# basalt/kevlar -> more durable but slightly damped; very wide (stiff) beams crack sooner.
FRAME_POWER = {"graphite": 0.0, "graphite-basalt": -0.5, "graphite-kevlar": -1.0}
FRAME_DURABILITY = {"graphite": 1.0, "graphite-basalt": 1.3, "graphite-kevlar": 1.6}
PATTERN_SPIN = {"16x19": 250, "18x20": 0}
FAILURE_MODE = {
    "graphite": "frame crack at throat",
    "graphite-basalt": "hoop crack at 10 o'clock",
    "graphite-kevlar": "hoop delamination",
}

# This week's follow-ups to the open issues: (racket_id, test_type, days_ago, note).
FOLLOW_UPS = [
    ("RK-100-A", "power", 2, "retest after radar recalibration"),
    ("RK-100-A", "power", 2, "retest after radar recalibration"),
    ("RK-100-A", "power", 1, "retest after radar recalibration"),
    ("RK-98-A", "durability", 1, "retest on layup batch B2; batch B1 quarantined"),
    ("RK-100-B", "durability", 3, "lead candidate: extra durability sample"),
    ("RK-100-B", "durability", 2, "lead candidate: extra durability sample"),
    ("RK-100-B", "durability", 1, "lead candidate: extra durability sample"),
    ("RK-100-B", "swingweight", 1, None),
    ("RK-100-D", "swingweight", 1, "first swingweight check"),
    ("RK-100-D", "spin", 1, None),
    ("RK-100-D", "spin", 0, None),
]


def simulate_run(rng: random.Random, test_type: str, racket: tuple):
    """Return (column, value, passed, notes) for one run of `test_type` on `racket`."""
    _, _, frame, head, beam, pattern, weight, _, _ = racket
    if test_type == "power":
        mph = round(64.5 + (beam - 21) + 0.15 * (head - 95) + FRAME_POWER[frame] + rng.gauss(0, 1.0), 1)
        passed = mph >= 63
        return "ball_exit_speed_mph", mph, passed, None if passed else f"{mph} mph below 63 mph spec"
    if test_type == "spin":
        rpm = round(2100 + PATTERN_SPIN[pattern] + 8 * (head - 95) + rng.gauss(0, 90))
        passed = rpm >= 2100
        return "spin_rpm", rpm, passed, None if passed else f"{rpm} rpm below 2100 rpm spec"
    if test_type == "durability":
        hits = round(55000 * FRAME_DURABILITY[frame] * (1 - 0.03 * (beam - 21)) * rng.lognormvariate(0, 0.2))
        passed = hits >= 40000
        return "hits_to_failure", hits, passed, None if passed else f"{FAILURE_MODE[frame]} at {hits / 1000:.0f}k impacts"
    sw = round(300 + 0.9 * (weight - 290) + 0.3 * (head - 95) + rng.gauss(0, 2), 1)
    passed = 300 <= sw <= 330
    return "swingweight", sw, passed, None if passed else f"swingweight {sw} outside 300-330 spec"


def insert_run(conn, rng, racket: tuple, test_type: str, run_date: date, note: str | None = None) -> None:
    column, value, passed, fail_note = simulate_run(rng, test_type, racket)
    notes = "; ".join(n for n in (fail_note, note) if n) or None
    # `column` comes from simulate_run's fixed set, never from user input.
    conn.execute(
        f"INSERT INTO test_runs (racket_id, test_type, run_date, technician, {column}, passed, notes)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        (racket[0], test_type, run_date.isoformat(), rng.choice(TECHNICIANS), value, passed, notes),
    )


def seed(path: str | Path, today: date | None = None) -> None:
    """(Re)create the database at `path`. Same rng seed -> same values; dates are relative to `today`."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.unlink(missing_ok=True)
    today = today or date.today()
    rng = random.Random(42)

    conn = sqlite3.connect(path)
    conn.executescript(SCHEMA)
    by_id = {r[0]: r for r in RACKETS}
    for racket in RACKETS:
        days_ago = racket[-1]
        created = today - timedelta(days=days_ago)
        conn.execute("INSERT INTO rackets VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                     (*racket[:-1], created.isoformat()))
        n_runs = 4 + days_ago // 14  # older rackets have been tested more
        # History ends 4 days ago, so this week's follow-ups are the newest runs.
        offsets = sorted(rng.randint(0, days_ago - 4) for _ in range(n_runs))
        for i, offset in enumerate(offsets):
            insert_run(conn, rng, racket, TEST_CYCLE[i % len(TEST_CYCLE)], created + timedelta(days=offset))

    # Two hand-placed outliers in the history, so the data has something worth investigating.
    conn.execute(
        "UPDATE test_runs SET ball_exit_speed_mph = 79.6, notes = 'suspected radar misread, retest requested'"
        " WHERE id = (SELECT id FROM test_runs WHERE racket_id = 'RK-100-A' AND test_type = 'power'"
        " ORDER BY run_date DESC LIMIT 1)"
    )
    conn.execute(
        "UPDATE test_runs SET hits_to_failure = 9800, passed = 0,"
        " notes = 'frame crack at throat at 10k impacts, suspect bad layup batch B1'"
        " WHERE id = (SELECT id FROM test_runs WHERE racket_id = 'RK-98-A' AND test_type = 'durability'"
        " ORDER BY run_date DESC LIMIT 1)"
    )

    for racket_id, test_type, days_ago, note in FOLLOW_UPS:
        insert_run(conn, rng, by_id[racket_id], test_type, today - timedelta(days=days_ago), note)
    conn.commit()
    conn.close()


if __name__ == "__main__":
    db_path = os.environ.get("RACKET_LAB_DB") or "data/lab.db"
    seed(db_path)
    print(f"Seeded {db_path}")
