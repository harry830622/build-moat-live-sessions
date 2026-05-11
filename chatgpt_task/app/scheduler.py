import os
import queue
import threading
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from .database import SessionLocal
from .models import Job, utcnow

job_queue: queue.Queue[int] = queue.Queue()

WORKER_COUNT = int(os.environ.get("WORKER_COUNT", "4"))
WATCHER_INTERVAL = 10


def get_time_bucket(scheduled_at: datetime) -> str:
    return scheduled_at.strftime("%Y%m%d%H")


def find_due_jobs(current_time: datetime, db: Session) -> list[Job]:
    curr = get_time_bucket(current_time)
    prev = get_time_bucket(current_time - timedelta(hours=1))
    return (
        db.query(Job)
        .filter(Job.time_bucket.in_([curr, prev]))
        .filter(Job.scheduled_at <= current_time)
        .filter(Job.status == "pending")
        .all()
    )


def watcher_loop() -> None:
    while True:
        db = SessionLocal()
        try:
            now = utcnow()
            for job in find_due_jobs(now, db):
                job.status = "queued"
                db.commit()
                job_queue.put(job.id)
        finally:
            db.close()
        time.sleep(WATCHER_INTERVAL)


def worker_loop() -> None:
    while True:
        job_id = job_queue.get()
        db = SessionLocal()
        try:
            job = db.query(Job).filter(Job.id == job_id).first()
            if job is None or job.status == "cancelled":
                continue
            job.status = "running"
            db.commit()
            try:
                job.result = f"Executed: {job.description}"
                job.status = "completed"
                db.commit()
            except Exception as e:
                job.status = "failed"
                job.result = str(e)
                db.commit()
        finally:
            db.close()
            job_queue.task_done()


def start_scheduler() -> None:
    threading.Thread(target=watcher_loop, daemon=True).start()
    for _ in range(WORKER_COUNT):
        threading.Thread(target=worker_loop, daemon=True).start()
