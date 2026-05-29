"""
FastAPI backend serving:
  - Agent control (start/stop jobs manually)
  - Message review queue (approve / edit / reject)
  - Stats & logs dashboard data
"""

import asyncio
import json
from datetime import datetime, date, timedelta
from typing import Optional
from contextlib import asynccontextmanager

from fastapi import FastAPI, Depends, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy.orm import Session
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from loguru import logger

from database.models import (
    create_tables, get_db, SessionLocal,
    Profile, Message, DailyStats, AgentLog,
    ConnectionStatus, MessageStatus, Persona,
)
from agent.orchestrator import (
    run_discovery_job,
    run_connection_monitor_job,
    run_send_approved_messages,
    shutdown_browser,
)
from agent.rate_limiter import get_weekly_stats
from config.settings import settings


# ─── Scheduler ────────────────────────────────────────────────

scheduler = AsyncIOScheduler()


@asynccontextmanager
async def lifespan(app: FastAPI):
    create_tables()
    logger.info("Database tables created")

    # Discovery: once a day at 10:30 AM
    scheduler.add_job(
        run_discovery_job,
        "cron", hour=10, minute=30,
        id="discovery_job",
        replace_existing=True,
    )
    # Connection monitor: every hour
    scheduler.add_job(
        run_connection_monitor_job,
        "interval", minutes=settings.poll_interval_minutes,
        id="connection_monitor",
        replace_existing=True,
    )
    # Send approved: every 30 minutes
    scheduler.add_job(
        run_send_approved_messages,
        "interval", minutes=30,
        id="send_approved",
        replace_existing=True,
    )

    scheduler.start()
    logger.info("Scheduler started")
    yield

    scheduler.shutdown()
    await shutdown_browser()
    logger.info("Shutdown complete")


app = FastAPI(
    title="LinkedIn AI Agent",
    description="Automated LinkedIn outreach with human review",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ─── Pydantic schemas ─────────────────────────────────────────

class MessageReviewAction(BaseModel):
    action: str            # "approve" | "reject" | "edit"
    edited_message: Optional[str] = None
    reviewer_notes: Optional[str] = None


class ManualJobTrigger(BaseModel):
    job: str               # "discovery" | "monitor" | "send"


# ─── Stats endpoint ───────────────────────────────────────────

@app.get("/api/stats")
def get_stats(db: Session = Depends(get_db)):
    weekly = get_weekly_stats(db)
    today_str = date.today().isoformat()
    today = db.query(DailyStats).filter(DailyStats.date == today_str).first()

    pending_count = db.query(Message).filter(
        Message.status == MessageStatus.PENDING_REVIEW
    ).count()
    approved_count = db.query(Message).filter(
        Message.status == MessageStatus.APPROVED
    ).count()
    sent_count = db.query(Message).filter(
        Message.status == MessageStatus.SENT
    ).count()
    replied_count = db.query(Message).filter(
        Message.status == MessageStatus.REPLIED
    ).count()
    total_profiles = db.query(Profile).count()
    connected_count = db.query(Profile).filter(
        Profile.connection_status == ConnectionStatus.CONNECTED
    ).count()
    pending_invites = db.query(Profile).filter(
        Profile.connection_status == ConnectionStatus.INVITE_PENDING
    ).count()

    return {
        "today": {
            "invites_sent": today.invites_sent if today else 0,
            "invites_limit": settings.max_invites_per_day,
            "messages_sent": today.messages_sent if today else 0,
            "profiles_discovered": today.profiles_discovered if today else 0,
        },
        "totals": {
            "profiles": total_profiles,
            "connected": connected_count,
            "pending_invites": pending_invites,
            "messages_pending_review": pending_count,
            "messages_approved": approved_count,
            "messages_sent": sent_count,
            "messages_replied": replied_count,
        },
        "weekly": weekly,
    }


# ─── Message review endpoints ─────────────────────────────────

@app.get("/api/messages/pending")
def get_pending_messages(db: Session = Depends(get_db)):
    messages = (
        db.query(Message)
        .filter(Message.status == MessageStatus.PENDING_REVIEW)
        .order_by(Message.generated_at.desc())
        .limit(50)
        .all()
    )
    result = []
    for msg in messages:
        profile = db.query(Profile).filter(Profile.id == msg.profile_id).first()
        result.append({
            "id": msg.id,
            "recipient_name": msg.recipient_name,
            "linkedin_url": msg.linkedin_url,
            "persona": msg.persona.value if msg.persona else "unknown",
            "generated_message": msg.generated_message,
            "final_message": msg.final_message,
            "ai_reasoning": msg.ai_reasoning,
            "char_count": len(msg.final_message or msg.generated_message),
            "generated_at": msg.generated_at.isoformat() if msg.generated_at else None,
            "profile": {
                "current_role": profile.current_role if profile else None,
                "current_company": profile.current_company if profile else None,
                "headline": profile.headline if profile else None,
                "match_score": profile.match_score if profile else None,
                "skills": profile.skills if profile else None,
                "hooks": json.loads(profile.personalisation_hooks or "[]") if profile else [],
            } if profile else {},
        })
    return result


@app.post("/api/messages/{message_id}/review")
def review_message(
    message_id: int,
    action: MessageReviewAction,
    db: Session = Depends(get_db),
):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")

    if action.action == "approve":
        msg.status = MessageStatus.APPROVED
        if action.edited_message:
            msg.final_message = action.edited_message
        msg.reviewer_notes = action.reviewer_notes
        msg.reviewed_at = datetime.utcnow()

    elif action.action == "reject":
        msg.status = MessageStatus.REJECTED
        msg.reviewer_notes = action.reviewer_notes
        msg.reviewed_at = datetime.utcnow()

    elif action.action == "edit":
        if not action.edited_message:
            raise HTTPException(status_code=400, detail="edited_message required for edit action")
        msg.final_message = action.edited_message
        msg.status = MessageStatus.APPROVED
        msg.reviewed_at = datetime.utcnow()

    else:
        raise HTTPException(status_code=400, detail="action must be approve | reject | edit")

    db.commit()
    return {"status": "ok", "message_status": msg.status.value}


@app.post("/api/messages/{message_id}/mark-replied")
def mark_replied(message_id: int, db: Session = Depends(get_db)):
    msg = db.query(Message).filter(Message.id == message_id).first()
    if not msg:
        raise HTTPException(status_code=404, detail="Message not found")
    msg.status = MessageStatus.REPLIED
    msg.replied_at = datetime.utcnow()
    db.commit()
    return {"status": "ok"}


# ─── Profile endpoints ────────────────────────────────────────

@app.get("/api/profiles")
def get_profiles(
    status: Optional[str] = None,
    persona: Optional[str] = None,
    min_score: float = 0,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    q = db.query(Profile)
    if status:
        q = q.filter(Profile.connection_status == ConnectionStatus(status))
    if persona:
        q = q.filter(Profile.persona == Persona(persona))
    if min_score:
        q = q.filter(Profile.match_score >= min_score)
    profiles = q.order_by(Profile.discovered_at.desc()).limit(limit).all()

    return [
        {
            "id": p.id,
            "linkedin_url": p.linkedin_url,
            "full_name": p.full_name,
            "headline": p.headline,
            "current_role": p.current_role,
            "current_company": p.current_company,
            "location": p.location,
            "persona": p.persona.value if p.persona else "unknown",
            "match_score": p.match_score,
            "connection_status": p.connection_status.value if p.connection_status else "discovered",
            "invite_sent_at": p.invite_sent_at.isoformat() if p.invite_sent_at else None,
            "connected_at": p.connected_at.isoformat() if p.connected_at else None,
            "skills": p.skills,
            "hooks": json.loads(p.personalisation_hooks or "[]"),
            "discovered_at": p.discovered_at.isoformat() if p.discovered_at else None,
        }
        for p in profiles
    ]


# ─── Logs endpoint ────────────────────────────────────────────

@app.get("/api/logs")
def get_logs(limit: int = 100, db: Session = Depends(get_db)):
    logs = (
        db.query(AgentLog)
        .order_by(AgentLog.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": l.id,
            "level": l.level,
            "event": l.event,
            "detail": l.detail,
            "profile_url": l.profile_url,
            "created_at": l.created_at.isoformat() if l.created_at else None,
        }
        for l in logs
    ]


# ─── Manual job trigger ───────────────────────────────────────

@app.post("/api/jobs/trigger")
async def trigger_job(
    trigger: ManualJobTrigger,
    background_tasks: BackgroundTasks,
):
    job_map = {
        "discovery": run_discovery_job,
        "monitor": run_connection_monitor_job,
        "send": run_send_approved_messages,
    }
    fn = job_map.get(trigger.job)
    if not fn:
        raise HTTPException(status_code=400, detail="Unknown job. Use: discovery | monitor | send")

    background_tasks.add_task(fn)
    return {"status": "triggered", "job": trigger.job}


# ─── Scheduler status ─────────────────────────────────────────

@app.get("/api/scheduler/status")
def scheduler_status():
    jobs = []
    for job in scheduler.get_jobs():
        jobs.append({
            "id": job.id,
            "next_run": str(job.next_run_time) if job.next_run_time else None,
        })
    return {"running": scheduler.running, "jobs": jobs}


# ─── Health check ─────────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok", "time": datetime.utcnow().isoformat()}


# ─── Serve dashboard ──────────────────────────────────────────

@app.get("/", response_class=HTMLResponse)
async def serve_dashboard():
    try:
        with open("dashboard/index.html") as f:
            return f.read()
    except FileNotFoundError:
        return HTMLResponse("<h1>Dashboard not found — run from project root</h1>", status_code=404)
