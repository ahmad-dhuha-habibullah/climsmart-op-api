"""Job worker: `python -m app.workers.run [--once] [--interval 2]`. Polls the jobs table and claims rows atomically."""
from __future__ import annotations

import argparse
import logging
import time

from app.api.v2 import build_context
from app.services import apsim, jobs, met  # noqa: F401  (register job handlers)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="process queued jobs until none are left, then exit")
    ap.add_argument("--interval", type=float, default=2.0)
    a = ap.parse_args()
    logging.basicConfig(level="INFO", format="%(asctime)s %(levelname)s %(name)s %(message)s")
    log = logging.getLogger("climsmart.worker")
    ctx = build_context()
    if ctx is None:
        raise SystemExit("DATABASE_URL is required")
    log.info("worker %s started (db=%s)", jobs.WORKER_ID, ctx.db.engine.dialect.name)
    while True:
        jid = jobs.claim(ctx)
        if jid:
            log.info("claimed %s", jid)
            jobs.execute(ctx, jid)
            continue
        if a.once:
            return
        time.sleep(a.interval)


if __name__ == "__main__":
    main()
