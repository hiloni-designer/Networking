"""
Rate limit guardian for LinkedIn invites.
Tracks daily counts in DB and enforces safe limits.
"""

from datetime import date, datetime
from loguru import logger
from sqlalchemy.orm import Session
from database.models import DailyStats, AgentLog
from config.settings import settings


def get_or_create_today(db: Session) -> DailyStats:
    today_str = date.today().isoformat()
    stats = db.query(DailyStats).filter(DailyStats.date == today_str).first()
    if not stats:
        stats = DailyStats(date=today_str)
        db.add(stats)
        db.commit()
        db.refresh(stats)
    return stats


def can_send_invite(db: Session) -> bool:
    """Returns True if we're under the daily invite limit."""
    stats = get_or_create_today(db)
    remaining = settings.max_invites_per_day - stats.invites_sent
    if remaining <= 0:
        logger.warning(
            f"Daily invite limit reached ({settings.max_invites_per_day}). "
            "Queuing remaining for tomorrow."
        )
        return False
    logger.info(f"Invite budget: {stats.invites_sent}/{settings.max_invites_per_day} used today ({remaining} remaining)")
    return True


def record_invite_sent(db: Session):
    stats = get_or_create_today(db)
    stats.invites_sent += 1
    db.commit()
    log_event(db, "INVITE_SENT", f"Daily total: {stats.invites_sent}/{settings.max_invites_per_day}")


def record_connection_accepted(db: Session, profile_url: str):
    stats = get_or_create_today(db)
    stats.invites_accepted += 1
    db.commit()
    log_event(db, "CONNECTION_ACCEPTED", profile_url=profile_url)


def record_message_sent(db: Session, profile_url: str):
    stats = get_or_create_today(db)
    stats.messages_sent += 1
    db.commit()
    log_event(db, "MESSAGE_SENT", profile_url=profile_url)


def record_profile_discovered(db: Session, count: int = 1):
    stats = get_or_create_today(db)
    stats.profiles_discovered += count
    db.commit()


def log_event(
    db: Session,
    event: str,
    detail: str = None,
    level: str = "INFO",
    profile_url: str = None,
):
    entry = AgentLog(
        level=level,
        event=event,
        detail=detail,
        profile_url=profile_url,
        created_at=datetime.utcnow(),
    )
    db.add(entry)
    db.commit()


def get_weekly_stats(db: Session) -> dict:
    """Return invite stats for the last 7 days."""
    from datetime import timedelta
    results = []
    today = date.today()
    for i in range(6, -1, -1):
        d = (today - timedelta(days=i)).isoformat()
        stats = db.query(DailyStats).filter(DailyStats.date == d).first()
        results.append({
            "date": d,
            "invites_sent": stats.invites_sent if stats else 0,
            "invites_accepted": stats.invites_accepted if stats else 0,
            "messages_sent": stats.messages_sent if stats else 0,
            "messages_replied": stats.messages_replied if stats else 0,
            "profiles_discovered": stats.profiles_discovered if stats else 0,
        })
    return results
