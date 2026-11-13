"""Tiny in-process job system. Long-running work (download, render, AI) runs in a thread pool;
the UI polls GET /api/jobs/{id}. A job function receives a JobContext."""
from __future__ import annotations

import threading
import time
import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable


class JobCancelled(Exception):
    pass


@dataclass
class Job:
    id: str
    kind: str
    title: str
    status: str = "queued"          # queued | running | done | error | cancelled
    progress: float = 0.0           # 0..1
    message: str = ""
    result: Any = None
    error: str | None = None
    created: float = field(default_factory=time.time)
    finished: float | None = None
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)
    _procs: list = field(default_factory=list, repr=False)

    def public(self) -> dict:
        return {
            "id": self.id, "kind": self.kind, "title": self.title, "status": self.status,
            "progress": round(self.progress, 4), "message": self.message,
            "result": self.result, "error": self.error,
            "created": self.created, "finished": self.finished,
        }


class JobContext:
    def __init__(self, job: Job):
        self.job = job

    def progress(self, p: float, message: str | None = None) -> None:
        self.job.progress = max(0.0, min(1.0, float(p)))
        if message is not None:
            self.job.message = message
        self.check()

    @property
    def cancelled(self) -> bool:
        return self.job._cancel.is_set()

    def check(self) -> None:
        if self.cancelled:
            raise JobCancelled()

    def register_process(self, proc) -> None:
        """Register a subprocess so cancel() can terminate it."""
        self.job._procs.append(proc)


class JobManager:
    def __init__(self, workers: int = 4):
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="job")
        self._jobs: dict[str, Job] = {}
        self._lock = threading.Lock()
        # called with the Job once it has finished (done / error / cancelled); must be quick and never raise
        self.listeners: list[Callable[[Job], None]] = []

    def submit(self, kind: str, title: str, fn: Callable[[JobContext], Any]) -> Job:
        job = Job(id=uuid.uuid4().hex[:12], kind=kind, title=title)
        with self._lock:
            self._jobs[job.id] = job

        def runner():
            ctx = JobContext(job)
            job.status = "running"
            try:
                job.result = fn(ctx)
                job.status = "done"
                job.progress = 1.0
            except JobCancelled:
                job.status = "cancelled"
            except Exception as e:  # noqa: BLE001
                job.status = "error"
                job.error = f"{type(e).__name__}: {e}"
                job.message = job.error
                traceback.print_exc()
            finally:
                job.finished = time.time()
                for listener in list(self.listeners):   # e.g. the project store's persistent activity history
                    try:
                        listener(job)
                    except Exception:  # noqa: BLE001
                        traceback.print_exc()

        self._pool.submit(runner)
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        return sorted(self._jobs.values(), key=lambda j: -j.created)

    def cancel(self, job_id: str) -> bool:
        j = self._jobs.get(job_id)
        if not j:
            return False
        j._cancel.set()
        for p in list(j._procs):
            try:
                p.terminate()
            except Exception:
                pass
        return True


jobs = JobManager()
