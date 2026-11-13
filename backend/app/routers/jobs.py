from __future__ import annotations

from fastapi import APIRouter, HTTPException

from ..jobs import jobs

router = APIRouter(prefix="/api/jobs", tags=["jobs"])


@router.get("")
def list_jobs(limit: int = 50):
    return [j.public() for j in jobs.list()[:limit]]


@router.get("/{job_id}")
def get_job(job_id: str):
    j = jobs.get(job_id)
    if not j:
        raise HTTPException(404, "job not found")
    return j.public()


@router.post("/{job_id}/cancel")
def cancel(job_id: str):
    if not jobs.cancel(job_id):
        raise HTTPException(404, "job not found")
    return {"ok": True}
