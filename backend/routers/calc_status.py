"""Async recompute status (E2E_ARCHITECTURE_AUDIT G2).

The cost recompute runs asynchronously on the calc_jobs queue, so a mutating
request can't tell the user when the derived costs are fresh again. This endpoint
exposes the queue state (optionally per ingredient) as a small HTMX-pollable badge
so dependent views stop showing a stale "recalculando…" forever and surface
dead-lettered jobs.
"""

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from backend.database import get_db
from backend.services.calc_worker import job_queue_status

router = APIRouter(prefix="/calc", tags=["calc-status"])
templates = Jinja2Templates(
    directory=str(Path(__file__).resolve().parent.parent / "templates")
)


@router.get("/status", response_class=HTMLResponse)
def calc_status_badge(
    request: Request,
    ingredient_id: Optional[int] = None,
    db: Session = Depends(get_db),
) -> HTMLResponse:
    status = job_queue_status(db, ingredient_id=ingredient_id)
    poll_url = "/calc/status" + (f"?ingredient_id={ingredient_id}" if ingredient_id else "")
    return templates.TemplateResponse("calc/_status_badge.html", {
        "request": request,
        "status": status,
        "poll_url": poll_url,
    })
