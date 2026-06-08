"""calc_jobs worker entrypoint (run as a separate Railway service / process).

Drains the recompute queue continuously: each tick reaps stale jobs (G1) then
claims and processes pending jobs (FOR UPDATE SKIP LOCKED) until the queue is
empty, then idles. Run N instances to scale horizontally — _PureCalculator is
stateless and claims are mutually exclusive.

    python -m backend.worker
"""

import logging
import time

from backend.database import ReadSessionLocal, SessionLocal
from backend.services.calc_worker import run_worker

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
log = logging.getLogger("calc-worker")

IDLE_SLEEP_SECONDS = 5


def main() -> None:
    log.info("calc worker started")
    while True:
        db = SessionLocal()
        # N3: read-heavy batch prefetch goes to the replica (or the primary as a
        # transparent fallback when DATABASE_URL_REPLICA is unset). Writes use db.
        read_db = ReadSessionLocal()
        try:
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
