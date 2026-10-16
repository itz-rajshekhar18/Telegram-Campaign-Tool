from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, JSON
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


Base = declarative_base()

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    phone = Column(String, unique=True)
    session_name = Column(String, unique=True)
    status = Column(String, default="idle")  # idle, active, restricted, banned, unauthorized
    created_at = Column(DateTime, default=utcnow)

class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    objective = Column(Text)
    info_bank = Column(Text)          # product details, pricing, FAQs, templates
    target_keywords = Column(Text)    # comma separated
    rate_profile = Column(String, default="balanced")  # safe / balanced / aggressive
    status = Column(String, default="draft")  # draft, running, paused, stopped
    created_at = Column(DateTime, default=utcnow)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)

class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    telegram_id = Column(String, unique=True)
    title = Column(String)
    username = Column(String, nullable=True)
    joined = Column(Boolean, default=False)
    can_send = Column(Boolean, default=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    joined_at = Column(DateTime, nullable=True)

class Lead(Base):
    __tablename__ = "leads"
    id = Column(Integer, primary_key=True)
    telegram_user_id = Column(String)
    username = Column(String, nullable=True)
    first_name = Column(String, nullable=True)
    language = Column(String, default="auto")  # hi / en / hinglish
    score = Column(String, default="cold")     # hot / warm / cold
    status = Column(String, default="new")
    notes = Column(Text, nullable=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    last_message_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    direction = Column(String)  # in / out
    content = Column(Text)
    is_voice = Column(Boolean, default=False)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    created_at = Column(DateTime, default=utcnow)

class Report(Base):
    __tablename__ = "reports"
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    date = Column(String)
    groups_joined = Column(Integer, default=0)
    messages_sent = Column(Integer, default=0)
    replies_received = Column(Integer, default=0)
    leads_captured = Column(Integer, default=0)
    summary = Column(Text)
    created_at = Column(DateTime, default=utcnow)
