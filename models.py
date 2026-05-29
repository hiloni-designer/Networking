from sqlalchemy import (
    create_engine, Column, String, Integer, Boolean,
    DateTime, Text, Float, Enum as SAEnum
)
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from datetime import datetime
import enum
from config.settings import settings


engine = create_engine(
    settings.database_url,
    connect_args={"check_same_thread": False} if "sqlite" in settings.database_url else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


# ─── Enums ────────────────────────────────────────────────────

class Persona(str, enum.Enum):
    SENIOR_DESIGNER = "senior_designer"
    DESIGN_LEADER = "design_leader"
    RECRUITER = "recruiter"
    HR = "hr"
    STARTUP_FOUNDER = "startup_founder"
    PRODUCT_MANAGER = "product_manager"
    FINTECH_PROFESSIONAL = "fintech_professional"
    UNKNOWN = "unknown"


class ConnectionStatus(str, enum.Enum):
    DISCOVERED = "discovered"       # Found in search
    INVITE_PENDING = "invite_pending"   # Invite sent, waiting
    CONNECTED = "connected"         # They accepted
    INVITE_FAILED = "invite_failed"     # Failed to send
    SKIPPED = "skipped"             # Filtered out (low score)


class MessageStatus(str, enum.Enum):
    PENDING_REVIEW = "pending_review"   # Waiting for human approval
    APPROVED = "approved"           # Human approved, not yet sent
    SENT = "sent"                   # Message delivered
    REJECTED = "rejected"           # Human rejected/edited
    REPLIED = "replied"             # They replied!


# ─── Models ───────────────────────────────────────────────────

class Profile(Base):
    __tablename__ = "profiles"

    id = Column(Integer, primary_key=True, index=True)
    linkedin_url = Column(String, unique=True, index=True, nullable=False)
    linkedin_id = Column(String, unique=True, nullable=True)

    # Basic info
    full_name = Column(String, nullable=True)
    headline = Column(String, nullable=True)
    current_role = Column(String, nullable=True)
    current_company = Column(String, nullable=True)
    location = Column(String, nullable=True)
    about = Column(Text, nullable=True)
    skills = Column(Text, nullable=True)          # JSON list stored as text
    profile_picture_url = Column(String, nullable=True)

    # Enrichment
    linked_website_url = Column(String, nullable=True)
    website_content = Column(Text, nullable=True) # Scraped text from their website

    # AI classification
    persona = Column(SAEnum(Persona), default=Persona.UNKNOWN)
    match_score = Column(Float, default=0.0)       # 0-10
    classification_notes = Column(Text, nullable=True)
    personalisation_hooks = Column(Text, nullable=True)  # JSON

    # Status
    connection_status = Column(SAEnum(ConnectionStatus), default=ConnectionStatus.DISCOVERED)
    invite_sent_at = Column(DateTime, nullable=True)
    connected_at = Column(DateTime, nullable=True)

    # Source
    search_query = Column(String, nullable=True)
    discovered_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    profile_id = Column(Integer, nullable=False, index=True)
    linkedin_url = Column(String, nullable=False)
    recipient_name = Column(String, nullable=True)
    persona = Column(SAEnum(Persona), nullable=True)

    # Content
    generated_message = Column(Text, nullable=False)
    final_message = Column(Text, nullable=True)    # After human edits
    ai_reasoning = Column(Text, nullable=True)     # Why this message was chosen

    # Review
    status = Column(SAEnum(MessageStatus), default=MessageStatus.PENDING_REVIEW)
    reviewer_notes = Column(Text, nullable=True)

    # Timestamps
    generated_at = Column(DateTime, default=datetime.utcnow)
    reviewed_at = Column(DateTime, nullable=True)
    sent_at = Column(DateTime, nullable=True)
    replied_at = Column(DateTime, nullable=True)


class DailyStats(Base):
    __tablename__ = "daily_stats"

    id = Column(Integer, primary_key=True, index=True)
    date = Column(String, unique=True, nullable=False)  # YYYY-MM-DD
    invites_sent = Column(Integer, default=0)
    invites_accepted = Column(Integer, default=0)
    messages_sent = Column(Integer, default=0)
    messages_replied = Column(Integer, default=0)
    profiles_discovered = Column(Integer, default=0)


class AgentLog(Base):
    __tablename__ = "agent_logs"

    id = Column(Integer, primary_key=True, index=True)
    level = Column(String, default="INFO")
    event = Column(String, nullable=False)
    detail = Column(Text, nullable=True)
    profile_url = Column(String, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)


# ─── DB helpers ───────────────────────────────────────────────

def create_tables():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
