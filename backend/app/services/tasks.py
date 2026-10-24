"""Background tasks (indexing, book writing, imports) and the scheduler for recurring jobs.

Tasks run in a small thread pool, each with its own database session, and report progress into
``background_tasks`` so the UI can show a percentage. Tests run them inline (``settings.inline_tasks``).
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
import logging
import threading
import time
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from ..models import BackgroundTask, ScheduledJob, utcnow

log = logging.getLogger("pathmind.tasks")

TaskFn = Callable[[Session, BackgroundTask, Callable[[float], None]], dict | None]


class TaskRunner:
    def __init__(self, session_factory: sessionmaker, inline: bool = False, workers: int = 2):
        self.session_factory = session_factory
        self.inline = inline
        self.pool = None if inline else ThreadPoolExecutor(max_workers=workers, thread_name_prefix="pm-task")

    def submit(self, kind: str, owner_id: str | None, payload: dict[str, Any], fn: TaskFn) -> str:
        with self.session_factory() as db:
            task = BackgroundTask(kind=kind[:30], owner_id=owner_id, payload=payload, status="queued")
            db.add(task)
            db.commit()
            task_id = task.id
        if self.inline:
            self._run(task_id, fn)
        else:
            assert self.pool is not None
            self.pool.submit(self._run, task_id, fn)
        return task_id

    def _run(self, task_id: str, fn: TaskFn) -> None:
        db = self.session_factory()
        try:
            task = db.get(BackgroundTask, task_id)
            if task is None or task.status == "canceled":
                return
            task.status, task.started_at = "running", utcnow()
            db.commit()
            last = [0.0]

            def report(p: float) -> None:
                p = max(0.0, min(1.0, float(p)))
                if p - last[0] >= 0.02 or p >= 1.0:
                    last[0] = p
                    with self.session_factory() as s2:
                        t = s2.get(BackgroundTask, task_id)
                        if t is not None:
                            t.progress = p
                            s2.commit()

            result = fn(db, task, report) or {}
            db.refresh(task)
            task.status, task.progress, task.result, task.finished_at = "done", 1.0, result, utcnow()
            db.commit()
        except Exception as exc:  # noqa: BLE001 - recorded on the task for the UI
            log.exception("task %s failed", task_id)
            db.rollback()
            task = db.get(BackgroundTask, task_id)
            if task is not None:
                task.status, task.error, task.finished_at = "failed", str(exc)[:2000], utcnow()
                db.commit()
        finally:
            db.close()

    def shutdown(self) -> None:
        if self.pool is not None:
            self.pool.shutdown(wait=False, cancel_futures=True)


def public(t: BackgroundTask) -> dict:
    iso = lambda d: d.isoformat() + "Z" if d else None  # noqa: E731
    return {
        "id": t.id, "kind": t.kind, "status": t.status, "progress": round(t.progress or 0, 3),
        "payload": t.payload or {}, "result": t.result or {}, "error": t.error,
        "created_at": iso(t.created_at), "started_at": iso(t.started_at), "finished_at": iso(t.finished_at),
    }


# ── Cron (subset: numbers, *, */n, a-b, lists) ────────────────────────────────


def _field(spec: str, lo: int, hi: int) -> set[int]:
    out: set[int] = set()
    for part in spec.split(","):
        step = 1
        if "/" in part:
            part, s = part.split("/", 1)
            step = max(1, int(s))
        if part in ("*", ""):
            a, b = lo, hi
        elif "-" in part:
            a, b = (int(x) for x in part.split("-", 1))
        else:
            a = b = int(part)
        out.update(range(max(lo, a), min(hi, b) + 1, step))
    return out


def next_run(cron: str, after: datetime) -> datetime:
    """Next minute (naive, same clock as ``after``) matching ``cron`` = 'min hour dom month dow'."""
    parts = cron.split()
    if len(parts) != 5:
        raise ValueError("Cron needs 5 fields: minute hour day month weekday.")
    mins, hours, doms, months, dows = (
        _field(parts[0], 0, 59), _field(parts[1], 0, 23), _field(parts[2], 1, 31),
        _field(parts[3], 1, 12), _field(parts[4], 0, 6),
    )
    t = (after + timedelta(minutes=1)).replace(second=0, microsecond=0)
    for _ in range(60 * 24 * 370):
        # cron weekday: 0 = Sunday; Python: Monday = 0
        if (t.minute in mins and t.hour in hours and t.day in doms and t.month in months
                and (t.weekday() + 1) % 7 in dows):
            return t
        t += timedelta(minutes=1)
    raise ValueError("Cron never matches.")


# Built-in jobs: (name, cron in the job's timezone, action type)
BUILTIN_JOBS = [
    ("Study reminders", "0 8 * * *", "study_reminder"),
    ("Verified documents due for review", "30 8 * * *", "review_due"),
    ("Billing housekeeping", "*/30 * * * *", "billing_housekeeping"),
    ("Monthly top contributors", "5 0 1 * *", "top_contributors"),
]

JobFn = Callable[[Session, ScheduledJob], str]
JOB_ACTIONS: dict[str, JobFn] = {}


def job_action(name: str):
    def deco(fn: JobFn) -> JobFn:
        JOB_ACTIONS[name] = fn
        return fn
    return deco


def _tz_offset(tz: str) -> timedelta:
    try:
        from zoneinfo import ZoneInfo

        return ZoneInfo(tz).utcoffset(utcnow()) or timedelta(0)
    except Exception:  # noqa: BLE001 - unknown zone: treat as UTC
        return timedelta(0)


def schedule_next(job: ScheduledJob, now_utc: datetime) -> None:
    off = _tz_offset(job.timezone)
    job.next_run_at = next_run(job.cron, now_utc + off) - off


def ensure_builtin_jobs(db: Session) -> None:
    existing = {j.action.get("type") for j in db.scalars(select(ScheduledJob).where(ScheduledJob.owner_id.is_(None)))}
    now = utcnow()
    for name, cron, action in BUILTIN_JOBS:
        if action not in existing:
            job = ScheduledJob(name=name, cron=cron, action={"type": action, "builtin": True})
            schedule_next(job, now)
            db.add(job)
    db.commit()


def run_job(db: Session, job: ScheduledJob) -> str:
    fn = JOB_ACTIONS.get((job.action or {}).get("type", ""))
    if fn is None:
        return "unknown action"
    return fn(db, job)


def run_due_jobs(session_factory: sessionmaker) -> None:
    now = utcnow()
    with session_factory() as db:
        due = db.scalars(select(ScheduledJob).where(
            ScheduledJob.is_enabled.is_(True), ScheduledJob.next_run_at <= now)).all()
        for job in due:
            try:
                msg = run_job(db, job)
                job.last_status = "ok"
                log.info("job %s: %s", job.name, msg)
            except Exception:  # noqa: BLE001
                db.rollback()
                log.exception("job %s failed", job.name)
                job = db.get(ScheduledJob, job.id)
                job.last_status = "error"
            job.last_run_at = now
            schedule_next(job, now)
            db.commit()


class Scheduler(threading.Thread):
    def __init__(self, session_factory: sessionmaker, interval: float = 60.0):
        super().__init__(daemon=True, name="pm-scheduler")
        self.session_factory = session_factory
        self.interval = interval
        self._stop = threading.Event()

    def run(self) -> None:
        time.sleep(5)
        while not self._stop.is_set():
            try:
                run_due_jobs(self.session_factory)
            except Exception:  # noqa: BLE001
                log.exception("scheduler tick failed")
            self._stop.wait(self.interval)

    def stop(self) -> None:
        self._stop.set()
