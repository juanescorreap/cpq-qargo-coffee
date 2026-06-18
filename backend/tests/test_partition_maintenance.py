"""0027 / #4: recipe_cost_snapshots monthly partitions + app-side maintenance.

Covers the schema repartition (yearly -> monthly) and the single-source-of-truth
DB function fn_run_partition_maintenance (roll-ahead creation, retention drop,
exactly-once-per-day claim), plus the worker's pure due-check.
"""

from datetime import date, datetime

from sqlalchemy import text
from sqlalchemy.orm import Session

from backend.services.calc_worker import _SEED_TZ, maintenance_due


def _children(db: Session) -> list[str]:
    rows = db.execute(text(
        "SELECT c.relname FROM pg_inherits i "
        "JOIN pg_class c ON c.oid = i.inhrelid "
        "JOIN pg_class p ON p.oid = i.inhparent "
        "WHERE p.relname = 'recipe_cost_snapshots' ORDER BY c.relname"
    )).scalars().all()
    return list(rows)


def _part_name(d: date) -> str:
    return f"recipe_cost_snapshots_{d:%Y_%m}"


def _add_months(d: date, n: int) -> date:
    """First day of the month n months after d's month."""
    y, m = d.year, d.month - 1 + n
    return date(y + m // 12, m % 12 + 1, 1)


def _run(db: Session, ahead: int = 3, retention: int = 18):
    row = db.execute(text(
        "SELECT created, dropped FROM "
        "public.fn_run_partition_maintenance(:by, :a, :r)"
    ), {"by": "test", "a": ahead, "r": retention}).first()
    return int(row.created), int(row.dropped)


# ── Schema ───────────────────────────────────────────────────────────────────

def test_snapshots_are_monthly_not_yearly(test_db: Session):
    children = _children(test_db)
    assert "recipe_cost_snapshots_default" in children
    # No yearly children survive the repartition.
    assert "recipe_cost_snapshots_2025" not in children
    assert "recipe_cost_snapshots_2026" not in children
    assert "recipe_cost_snapshots_2027" not in children
    # At least one monthly child of the form _YYYY_MM exists.
    monthly = [c for c in children
               if c.startswith("recipe_cost_snapshots_")
               and c != "recipe_cost_snapshots_default"
               and len(c.rsplit("recipe_cost_snapshots_", 1)[1]) == 7]
    assert monthly, f"expected monthly partitions, got {children}"


def test_objects_exist(test_db: Session):
    assert test_db.execute(text(
        "SELECT to_regclass('public.maintenance_runs')")).scalar() is not None
    names = test_db.execute(text(
        "SELECT proname FROM pg_proc WHERE proname IN "
        "('ensure_month_partition','fn_run_partition_maintenance')"
    )).scalars().all()
    assert set(names) == {"ensure_month_partition", "fn_run_partition_maintenance"}


# ── fn_run_partition_maintenance behaviour ───────────────────────────────────

def test_maintenance_creates_partitions_ahead(test_db: Session):
    today = (datetime.now(_SEED_TZ)).date()
    target = _add_months(today.replace(day=1), 3)
    name = _part_name(target)

    # Clear today's claim (may exist in prod from the real worker) within this
    # transaction so the function can run; the outer rollback restores it.
    test_db.execute(text(
        "DELETE FROM public.maintenance_runs "
        "WHERE run_kind='partitions' "
        "AND run_date=(now() AT TIME ZONE 'America/Bogota')::date"
    ))

    # The migration already created the +3 window; drop one to force recreation.
    test_db.execute(text(f"DROP TABLE IF EXISTS public.{name}"))
    assert test_db.execute(text(
        f"SELECT to_regclass('public.{name}')")).scalar() is None

    created, _ = _run(test_db, ahead=3, retention=18)
    assert created >= 1
    assert test_db.execute(text(
        f"SELECT to_regclass('public.{name}')")).scalar() is not None


def test_maintenance_retention_drops_old_months(test_db: Session):
    # Clear today's claim (may exist in prod from the real worker) within this
    # transaction so the function can run; the outer rollback restores it.
    test_db.execute(text(
        "DELETE FROM public.maintenance_runs "
        "WHERE run_kind='partitions' "
        "AND run_date=(now() AT TIME ZONE 'America/Bogota')::date"
    ))

    # A partition far outside the retention window.
    test_db.execute(text(
        "CREATE TABLE IF NOT EXISTS public.recipe_cost_snapshots_2000_01 "
        "PARTITION OF public.recipe_cost_snapshots "
        "FOR VALUES FROM ('2000-01-01') TO ('2000-02-01')"
    ))
    today = (datetime.now(_SEED_TZ)).date()
    in_window = _part_name(today.replace(day=1))

    created, dropped = _run(test_db, ahead=3, retention=18)

    assert dropped >= 1
    assert test_db.execute(text(
        "SELECT to_regclass('public.recipe_cost_snapshots_2000_01')"
    )).scalar() is None
    # Default + the current month survive retention.
    assert test_db.execute(text(
        "SELECT to_regclass('public.recipe_cost_snapshots_default')"
    )).scalar() is not None
    assert test_db.execute(text(
        f"SELECT to_regclass('public.{in_window}')")).scalar() is not None


def test_maintenance_claim_is_exactly_once_per_day(test_db: Session):
    # First call wins the day's claim and does work; the second no-ops.
    _run(test_db)
    created2, dropped2 = _run(test_db)
    assert (created2, dropped2) == (0, 0)


def test_retention_never_touches_default(test_db: Session):
    # Even with an aggressive 1-month window, the default partition is spared
    # (the retention filter matches only _YYYY_MM children).
    _run(test_db, ahead=1, retention=1)
    assert test_db.execute(text(
        "SELECT to_regclass('public.recipe_cost_snapshots_default')"
    )).scalar() is not None


# ── worker due-check (pure) ──────────────────────────────────────────────────

def test_maintenance_due_pure():
    after_hour = datetime(2026, 6, 9, 4, tzinfo=_SEED_TZ)  # 04:00 >= seed hour 3
    before_hour = datetime(2026, 6, 9, 1, tzinfo=_SEED_TZ)
    assert maintenance_due(after_hour, None) is True
    assert maintenance_due(after_hour, date(2026, 6, 9)) is False  # already ran
    assert maintenance_due(after_hour, date(2026, 6, 8)) is True   # new day
    assert maintenance_due(before_hour, None) is False             # too early
