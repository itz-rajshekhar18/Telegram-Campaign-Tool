from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, Float, ForeignKey, JSON, UniqueConstraint
from sqlalchemy.orm import declarative_base
from datetime import datetime

Base = declarative_base()

class Account(Base):
    __tablename__ = "accounts"
    id = Column(Integer, primary_key=True)
    phone = Column(String, unique=True)
    session_name = Column(String, unique=True)
    status = Column(String, default="idle")  # idle, active, restricted, banned
    created_at = Column(DateTime, default=datetime.utcnow)

class Campaign(Base):
    __tablename__ = "campaigns"
    id = Column(Integer, primary_key=True)
    name = Column(String)
    objective = Column(Text)
    info_bank = Column(Text)          # product details, pricing, FAQs, templates
    target_keywords = Column(Text)    # comma separated
    rate_profile = Column(String, default="balanced")  # safe / balanced / aggressive
    status = Column(String, default="draft")  # draft, running, paused, stopped
    created_at = Column(DateTime, default=datetime.utcnow)
    account_id = Column(Integer, ForeignKey("accounts.id"), nullable=True)
    audience = Column(Text, default="")
    audience_size = Column(Integer, default=0)
    product_name = Column(String, default="")
    value_proposition = Column(Text, default="")
    proof = Column(Text, default="")
    offer = Column(Text, default="")
    call_to_action = Column(String, default="")
    primary_metric = Column(String, default="conversions")
    target_value = Column(Integer, default=0)
    deadline = Column(String, default="")
    consent_confirmed = Column(Boolean, default=False)
    telegram_user_id = Column(Integer, nullable=True, index=True)
    category = Column(String, default="general")
    budget = Column(Float, default=0)
    currency = Column(String, default="INR")
    landing_page = Column(Text, default="")
    language = Column(String, default="English")
    tone = Column(String, default="friendly")
    tags = Column(String, default="")
    internal_notes = Column(Text, default="")

class CampaignVariant(Base):
    __tablename__ = "campaign_variants"
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    label = Column(String, nullable=False)
    attention = Column(Text, default="")
    interest = Column(Text, default="")
    desire = Column(Text, default="")
    action = Column(Text, default="")
    score = Column(Integer, default=0)
    score_details = Column(JSON, default=dict)
    created_at = Column(DateTime, default=datetime.utcnow)

    @property
    def copy(self):
        return "\n\n".join(part for part in (self.attention, self.interest, self.desire, self.action) if part)

class MetricSnapshot(Base):
    __tablename__ = "metric_snapshots"
    __table_args__ = (UniqueConstraint("variant_id", "period", name="uq_metric_variant_period"),)
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("campaign_variants.id"), nullable=False)
    period = Column(String, nullable=False)
    delivered = Column(Integer, default=0)
    clicks = Column(Integer, default=0)
    conversions = Column(Integer, default=0)
    replies = Column(Integer, default=0)
    signups = Column(Integer, default=0)
    opt_outs = Column(Integer, default=0)
    complaints = Column(Integer, default=0)
    spend = Column(Float, default=0.0)
    created_at = Column(DateTime, default=datetime.utcnow)

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
    created_at = Column(DateTime, default=datetime.utcnow)

class Message(Base):
    __tablename__ = "messages"
    id = Column(Integer, primary_key=True)
    direction = Column(String)  # in / out
    content = Column(Text)
    is_voice = Column(Boolean, default=False)
    lead_id = Column(Integer, ForeignKey("leads.id"), nullable=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"))
    created_at = Column(DateTime, default=datetime.utcnow)

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
    created_at = Column(DateTime, default=datetime.utcnow)


class Subscriber(Base):
    __tablename__ = "subscribers"
    __table_args__ = (UniqueConstraint("campaign_id", "chat_id"),)
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    chat_id = Column(Integer, nullable=False)
    name = Column(String, default="")
    username = Column(String, default="")
    active = Column(Boolean, default=True)
    consent_source = Column(String, nullable=False)
    notes = Column(Text, default="")
    stage = Column(String, default="new")
    created_at = Column(DateTime, default=datetime.utcnow)


class CampaignLink(Base):
    __tablename__ = "campaign_links"
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), primary_key=True)
    token = Column(String, unique=True, nullable=False)


class Broadcast(Base):
    __tablename__ = "broadcasts"
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    variant_id = Column(Integer, ForeignKey("campaign_variants.id"), nullable=False)
    text = Column(Text, nullable=False)
    status = Column(String, default="queued")
    scheduled_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class Delivery(Base):
    __tablename__ = "deliveries"
    __table_args__ = (UniqueConstraint("broadcast_id", "subscriber_id"),)
    id = Column(Integer, primary_key=True)
    broadcast_id = Column(Integer, ForeignKey("broadcasts.id"), nullable=False)
    subscriber_id = Column(Integer, ForeignKey("subscribers.id"), nullable=False)
    status = Column(String, default="pending")
    message_id = Column(Integer, nullable=True)
    error = Column(String, default="")
    sent_at = Column(DateTime, nullable=True)


class InboxMessage(Base):
    __tablename__ = "inbox_messages"
    id = Column(Integer, primary_key=True)
    update_id = Column(Integer, unique=True, nullable=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False)
    subscriber_id = Column(Integer, ForeignKey("subscribers.id"), nullable=False)
    direction = Column(String, default="in")
    text = Column(Text, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)


class BotCursor(Base):
    __tablename__ = "bot_cursor"
    id = Column(Integer, primary_key=True)
    offset = Column(Integer, default=0)


class Conversion(Base):
    __tablename__ = "conversions"
    __table_args__ = (UniqueConstraint("campaign_id", "reference", name="uq_conversion_reference"),)
    id = Column(Integer, primary_key=True)
    campaign_id = Column(Integer, ForeignKey("campaigns.id"), nullable=False, index=True)
    subscriber_id = Column(Integer, ForeignKey("subscribers.id"), nullable=True)
    variant_id = Column(Integer, ForeignKey("campaign_variants.id"), nullable=True)
    name = Column(String, nullable=False)
    kind = Column(String, default="purchase")
    status = Column(String, default="confirmed")
    amount = Column(Float, default=0)
    currency = Column(String, default="INR")
    source = Column(String, default="telegram")
    reference = Column(String, nullable=True)
    occurred_on = Column(String, nullable=False, index=True)
    notes = Column(Text, default="")
    created_at = Column(DateTime, default=datetime.utcnow)


class SavedReport(Base):
    __tablename__ = "saved_reports"
    __table_args__ = (UniqueConstraint("owner_id", "scope_id", "start", "end", "currency", name="uq_saved_report_scope"),)
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, nullable=False)
    scope_id = Column(Integer, nullable=False, default=0)
    start = Column(String, nullable=False)
    end = Column(String, nullable=False)
    currency = Column(String, nullable=False)
    payload = Column(JSON, nullable=False)
    saved_at = Column(DateTime, default=datetime.utcnow, nullable=False)


class WorkspaceSettings(Base):
    __tablename__ = "workspace_settings"
    owner_id = Column(Integer, primary_key=True)
    values = Column(JSON, nullable=False)


class DirectSendRecord(Base):
    __tablename__ = "direct_send_records"
    id = Column(Integer, primary_key=True)
    owner_id = Column(Integer, nullable=False, index=True)
    bot_id = Column(String, nullable=False)
    text = Column(Text, nullable=False)
    results = Column(JSON, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
