"""calc_jobs worker entrypoint (run as a separate Railway service / process).

Drains the recompute queue continuously: each tick reaps stale jobs (G1) then
claims and processes pending jobs (FOR UPDATE SKIP LOCKED) until the queue is
empty, then idles. Run N instances to scale horizontally — _PureCalculator is
stateless and claims are mutually exclusive.

    python -m backend.worker
"""

import logging
import time
from datetime import datetime

from backend.database import ReadSessionLocal, SessionLocal
from backend.services.calc_worker import (
    _SEED_TZ,
    maintenance_due,
    run_partition_maintenance,
    run_worker,
    seed_due,
    seed_nightly_recompute,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("calc-worker")

IDLE_SLEEP_SECONDS = 5


def main() -> None:
    log.info("calc worker started")
    # In-process guard so we attempt the nightly seed at most once per business
    # day per worker (the DB function dedups across workers); avoids a write every
    # tick. None until the first attempt of the process's current business day.
    last_seed_date = None
    # Same in-process guard for daily partition maintenance (0027 / #4).
    last_maint_date = None
    while True:
        db = SessionLocal()
        # N3: read-heavy batch prefetch goes to the replica (or the primary as a
        # transparent fallback when DATABASE_URL_REPLICA is unset). Writes use db.
        read_db = ReadSessionLocal()
        try:
            # N6: app-side nightly full-recompute seed (don't depend on pg_cron,
            # which is absent on Supabase). Exactly-once/day is enforced in-DB.
            now = datetime.now(_SEED_TZ)
            if seed_due(now, last_seed_date):
                try:
                    seeded = seed_nightly_recompute(db, by="worker")
                    if seeded:
                        log.info("nightly recompute seeded: %d jobs", seeded)
                    last_seed_date = now.date()
                except Exception:  # noqa: BLE001 — seed failure must not kill the loop
                    log.exception("nightly seed failed; will retry next tick")
                    db.rollback()

            # 0027/#4: app-side partition maintenance (roll monthly snapshot
            # partitions forward + retention). Belt-and-suspenders next to the
            # pg_cron partition_maintenance job; exactly-once/day enforced in-DB.
            if maintenance_due(now, last_maint_date):
                try:
                    created, dropped = run_partition_maintenance(db, by="worker")
                    if created or dropped:
                        log.info(
                            "partition maintenance: +%d partitions, -%d partitions",
                            created, dropped,
                        )
                    last_maint_date = now.date()
                except Exception:  # noqa: BLE001 — must not kill the loop
                    log.exception("partition maintenance failed; will retry next tick")
                    db.rollback()

            processed = run_worker(db, read_db=read_db)
            if processed == 0:
                time.sleep(IDLE_SLEEP_SECONDS)
        except Exception:  # noqa: BLE001 — keep the loop alive
            log.exception("worker loop error")
            time.sleep(IDLE_SLEEP_SECONDS)
        finally:
            read_db.close()
            db.close()


if __name__ == "__main__":
    main()
