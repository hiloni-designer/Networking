"""
Main agent orchestrator.
Runs two scheduled jobs:
  1. Discovery job  — search LinkedIn, scrape, classify, send invites
  2. Connection job — detect new connections, generate + queue messages
"""

import asyncio
import json
import random
from datetime import datetime
from loguru import logger
from sqlalchemy.orm import Session

from config.settings import settings
from database.models import (
    SessionLocal, Profile, Message,
    Persona, ConnectionStatus, MessageStatus,
)
from agent.linkedin_browser import LinkedInBrowser
from agent.ai_service import classify_profile, generate_message, summarise_website
from agent.web_scraper import scrape_website
from agent.rate_limiter import (
    can_send_invite, record_invite_sent, record_connection_accepted,
    record_message_sent, record_profile_discovered, log_event,
)


# ─── Shared browser singleton ─────────────────────────────────

_browser: LinkedInBrowser | None = None


async def get_browser() -> LinkedInBrowser:
    global _browser
    if _browser is None:
        _browser = LinkedInBrowser()
        await _browser.start(headless=True)
    if not await _browser.ensure_logged_in():
        raise RuntimeError("LinkedIn login failed — check credentials or handle CAPTCHA")
    return _browser


# ─── Profile pipeline ─────────────────────────────────────────

async def process_profile(profile_url: str, query: str, db: Session) -> bool:
    """
    Full pipeline for a newly discovered profile:
    scrape → enrich website → classify → score → (if good) send invite.
    Returns True if an invite was sent.
    """
    # Skip if we've already seen this profile
    existing = db.query(Profile).filter(Profile.linkedin_url == profile_url).first()
    if existing:
        logger.debug(f"Already processed: {profile_url}")
        return False

    browser = await get_browser()

    # 1. Scrape LinkedIn profile
    logger.info(f"Scraping profile: {profile_url}")
    profile_data = await browser.scrape_profile(profile_url)
    await asyncio.sleep(random.uniform(settings.min_delay_seconds, settings.max_delay_seconds))

    # 2. Scrape linked website if present
    website_content = ""
    website_summary = ""
    if profile_data.get("linked_website_url"):
        raw_content = await scrape_website(profile_data["linked_website_url"])
        if raw_content:
            website_summary = summarise_website(
                profile_data["linked_website_url"], raw_content
            )
            website_content = website_summary

    profile_data["website_content"] = website_content or ""

    # 3. AI Classification
    classification = classify_profile(profile_data)
    persona_str = classification["persona"]
    match_score = classification["match_score"]

    # 4. Save to DB
    profile = Profile(
        linkedin_url=profile_url,
        full_name=profile_data.get("full_name"),
        headline=profile_data.get("headline"),
        current_role=profile_data.get("current_role"),
        current_company=profile_data.get("current_company"),
        location=profile_data.get("location"),
        about=profile_data.get("about"),
        skills=profile_data.get("skills"),
        linked_website_url=profile_data.get("linked_website_url"),
        website_content=website_content,
        profile_picture_url=profile_data.get("profile_picture_url"),
        persona=Persona(persona_str),
        match_score=match_score,
        classification_notes=classification.get("classification_notes"),
        personalisation_hooks=json.dumps(classification.get("personalisation_hooks", [])),
        connection_status=ConnectionStatus.DISCOVERED,
        search_query=query,
    )
    db.add(profile)
    db.commit()
    db.refresh(profile)
    record_profile_discovered(db)

    # 5. Skip low-score profiles
    if match_score < 4.0 or persona_str == "unknown":
        logger.info(
            f"Skipping {profile_data.get('full_name')} — "
            f"score {match_score} / persona {persona_str}"
        )
        profile.connection_status = ConnectionStatus.SKIPPED
        db.commit()
        return False

    # 6. Check daily limit
    if not can_send_invite(db):
        logger.info("Daily limit hit — profile saved, invite queued for tomorrow")
        return False

    # 7. Send invite
    sent = await browser.send_connection_invite(profile_url)
    await asyncio.sleep(random.uniform(settings.min_delay_seconds, settings.max_delay_seconds))

    if sent:
        profile.connection_status = ConnectionStatus.INVITE_PENDING
        profile.invite_sent_at = datetime.utcnow()
        db.commit()
        record_invite_sent(db)
        log_event(
            db, "INVITE_SENT",
            f"{profile_data.get('full_name')} | {persona_str} | score {match_score}",
            profile_url=profile_url,
        )
        return True
    else:
        profile.connection_status = ConnectionStatus.INVITE_FAILED
        db.commit()
        return False


# ─── Discovery job ────────────────────────────────────────────

async def run_discovery_job():
    """
    Search LinkedIn using configured queries and process discovered profiles.
    Respects daily invite limits.
    """
    logger.info("=== Discovery job started ===")
    db = SessionLocal()
    try:
        browser = await get_browser()
        queries = settings.search_queries
        random.shuffle(queries)  # Vary order each run

        for query in queries:
            # Check limit before each query
            if not can_send_invite(db):
                logger.info("Daily limit reached — stopping discovery")
                break

            logger.info(f"Searching: '{query}'")
            profiles = await browser.search_people(query, max_results=10)
            await asyncio.sleep(random.uniform(5, 12))

            for p in profiles:
                if not can_send_invite(db):
                    break
                await process_profile(p["url"], query, db)
                # Natural pause between profiles
                await asyncio.sleep(random.uniform(settings.min_delay_seconds, settings.max_delay_seconds))

    except Exception as e:
        logger.error(f"Discovery job error: {e}")
        log_event(db, "DISCOVERY_ERROR", str(e), level="ERROR")
    finally:
        db.close()
    logger.info("=== Discovery job complete ===")


# ─── Connection monitor job ───────────────────────────────────

async def run_connection_monitor_job():
    """
    Check for newly accepted connections.
    For each new connection: generate a message and queue it for review.
    """
    logger.info("=== Connection monitor started ===")
    db = SessionLocal()
    try:
        browser = await get_browser()
        recent = await browser.get_recent_connections(max_items=40)

        for conn in recent:
            profile_url = conn["url"]

            # Find this profile in our DB
            profile = db.query(Profile).filter(
                Profile.linkedin_url == profile_url
            ).first()

            if not profile:
                # A connection we didn't initiate — still create a minimal record
                profile = Profile(
                    linkedin_url=profile_url,
                    full_name=conn.get("name"),
                    connection_status=ConnectionStatus.CONNECTED,
                    connected_at=datetime.utcnow(),
                )
                db.add(profile)
                db.commit()
                db.refresh(profile)

            # Skip if already connected (processed)
            if profile.connection_status == ConnectionStatus.CONNECTED:
                # Check if message already queued
                existing_msg = db.query(Message).filter(
                    Message.profile_id == profile.id
                ).first()
                if existing_msg:
                    continue  # Already handled

            # Mark as connected
            if profile.connection_status != ConnectionStatus.CONNECTED:
                profile.connection_status = ConnectionStatus.CONNECTED
                profile.connected_at = datetime.utcnow()
                db.commit()
                record_connection_accepted(db, profile_url)

            # Generate message
            logger.info(f"Generating message for new connection: {profile.full_name}")

            # If profile was from our DB, we have classification data
            classification = {
                "persona": profile.persona.value if profile.persona else "unknown",
                "match_score": profile.match_score or 5.0,
                "personalisation_hooks": json.loads(profile.personalisation_hooks or "[]"),
            }

            profile_data = {
                "full_name": profile.full_name,
                "headline": profile.headline,
                "current_role": profile.current_role,
                "current_company": profile.current_company,
                "skills": profile.skills,
                "website_content": profile.website_content,
            }

            # If minimal profile, re-scrape for richer data
            if not profile.current_role:
                logger.info(f"Re-scraping {profile_url} for richer data")
                scraped = await browser.scrape_profile(profile_url)
                profile_data.update(scraped)
                if not profile.persona or profile.persona == Persona.UNKNOWN:
                    classification = classify_profile(profile_data)

            gen = generate_message(profile_data, classification)

            if not gen["message"]:
                logger.warning(f"Empty message generated for {profile.full_name}")
                continue

            # Queue for human review
            msg = Message(
                profile_id=profile.id,
                linkedin_url=profile_url,
                recipient_name=profile.full_name,
                persona=Persona(classification["persona"]),
                generated_message=gen["message"],
                final_message=gen["message"],
                ai_reasoning=gen["reasoning"],
                status=MessageStatus.PENDING_REVIEW,
                generated_at=datetime.utcnow(),
            )
            db.add(msg)
            db.commit()

            log_event(
                db, "MESSAGE_QUEUED",
                f"{profile.full_name} | {gen['char_count']} chars",
                profile_url=profile_url,
            )
            logger.info(f"Message queued for review: {profile.full_name}")
            await asyncio.sleep(random.uniform(3, 8))

    except Exception as e:
        logger.error(f"Connection monitor error: {e}")
        log_event(db, "MONITOR_ERROR", str(e), level="ERROR")
    finally:
        db.close()
    logger.info("=== Connection monitor complete ===")


# ─── Send approved messages ───────────────────────────────────

async def run_send_approved_messages():
    """
    Send all messages that have been approved by the human reviewer.
    """
    logger.info("=== Sending approved messages ===")
    db = SessionLocal()
    try:
        browser = await get_browser()
        approved = db.query(Message).filter(
            Message.status == MessageStatus.APPROVED
        ).all()

        logger.info(f"{len(approved)} messages approved and ready to send")

        for msg in approved:
            text = msg.final_message or msg.generated_message
            sent = await browser.send_message(msg.linkedin_url, text)
            await asyncio.sleep(random.uniform(settings.min_delay_seconds, settings.max_delay_seconds))

            if sent:
                msg.status = MessageStatus.SENT
                msg.sent_at = datetime.utcnow()
                db.commit()
                record_message_sent(db, msg.linkedin_url)
                log_event(db, "MESSAGE_SENT", profile_url=msg.linkedin_url)
            else:
                log_event(
                    db, "MESSAGE_SEND_FAILED",
                    level="ERROR",
                    profile_url=msg.linkedin_url,
                )

    except Exception as e:
        logger.error(f"Send approved error: {e}")
    finally:
        db.close()
    logger.info("=== Send approved messages complete ===")


# ─── Cleanup ──────────────────────────────────────────────────

async def shutdown_browser():
    global _browser
    if _browser:
        await _browser.stop()
        _browser = None
