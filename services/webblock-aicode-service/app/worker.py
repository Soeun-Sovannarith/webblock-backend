"""Background worker that executes queued harness runs.

Runs in-process with the API by default (RUN_WORKER=true). For a separate worker process:
    RUN_WORKER=false uvicorn app.main:app   # API only
    python -m app.worker                    # one or more workers
Workers coordinate through Postgres (FOR UPDATE SKIP LOCKED + leases), so several can run at once.
"""
import logging
import threading
from app.core.config import settings
from app.services import store
from app.services.pipeline import execute_run

log = logging.getLogger("webblock.worker")

class Worker:
    def __init__(self) -> None:
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self.run_forever, name="harness-worker", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout)

    def run_forever(self) -> None:
        log.info("harness worker started")
        while not self._stop.is_set():
            try:
                if n := store.reap_expired_runs():
                    log.warning("requeued or failed %d run(s) with expired leases", n)
                run_id = store.claim_next_run()
            except Exception:  # keep polling through transient DB errors
                log.exception("worker poll failed")
                self._stop.wait(settings.worker_poll_seconds * 5)
                continue
            if run_id:
                log.info("executing run %s", run_id)
                execute_run(run_id)
            else:
                self._stop.wait(settings.worker_poll_seconds)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    try:
        Worker().run_forever()
    except KeyboardInterrupt:
        pass
