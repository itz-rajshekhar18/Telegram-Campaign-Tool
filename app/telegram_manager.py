import asyncio
from collections import deque
from datetime import datetime, timedelta, timezone
import logging
from logging.handlers import RotatingFileHandler
import os
import random
import re
from typing import Optional, Tuple, Dict, Any

from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from telethon import TelegramClient, events
from telethon.errors import (
    FloodWaitError,
    SlowModeWaitError,
    PeerFloodError,
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    ChannelPrivateError,
    ChatAdminRequiredError,
    UsersTooMuchError,
    ChannelsTooMuchError,
    InviteHashExpiredError,
    InviteHashInvalidError,
    UsernameInvalidError,
    UsernameNotOccupiedError,
    UserAlreadyParticipantError,
    AuthKeyUnregisteredError,
    UserDeactivatedError,
    UserDeactivatedBanError,
    SessionRevokedError,
    RPCError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.contacts import SearchRequest
from telethon.tl.functions.messages import ImportChatInviteRequest

from app.ai_engine import generate_reply, score_lead
from app.config import (
    API_ID,
    API_HASH,
    RATE_PROFILES,
    SESSIONS_DIR,
    LOGS_DIR,
    MAX_RECONNECT_ATTEMPTS,
    RECONNECT_BASE_DELAY,
    FLOOD_WAIT_BUFFER_SECONDS,
    MAX_INLINE_FLOOD_WAIT,
    PEER_FLOOD_COOLDOWN,
    USER_REPLY_COOLDOWN,
    GROUP_REPLY_COOLDOWN,
    WORKER_HEARTBEAT_SECONDS,
)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


os.makedirs(SESSIONS_DIR, exist_ok=True)
os.makedirs(LOGS_DIR, exist_ok=True)

# Configure structured worker logger
logger = logging.getLogger("campaign_worker")
if not logger.handlers:
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = RotatingFileHandler(
        os.path.join(LOGS_DIR, "campaign_worker.log"),
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    file_handler.setFormatter(fmt)
    stream_handler = logging.StreamHandler()
    stream_handler.setFormatter(fmt)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)

_DEVANAGARI_RE = re.compile(r"[\u0900-\u097F]")
_HINGLISH_WORDS = {
    "kya", "hai", "nahi", "kaise", "kitna", "kitne", "batao", "bhai",
    "chahiye", "milega", "karo", "aur", "mein", "apka", "aap", "hum",
    "accha", "theek", "haan", "जी", "sir", "madam", "fees", "paisa",
    "kab", "kahan", "kyu", "kyun", "mujhe", "mera", "hamara",
}

FATAL_AUTH_ERRORS = (
    AuthKeyUnregisteredError,
    UserDeactivatedError,
    UserDeactivatedBanError,
    SessionRevokedError,
)

WRITE_FORBIDDEN_ERRORS = (
    ChatWriteForbiddenError,
    UserBannedInChannelError,
    ChannelPrivateError,
    ChatAdminRequiredError,
)


def detect_language(text: str) -> str:
    """Detect whether message text is Hindi (Devanagari), Hinglish, or English."""
    if not text or not text.strip():
        return "auto"
    if _DEVANAGARI_RE.search(text):
        return "hi"
    tokens = set(re.findall(r"[a-zA-Z]+", text.lower()))
    if len(tokens.intersection(_HINGLISH_WORDS)) >= 1:
        return "hinglish"
    return "en"


class TelegramManager:
    def __init__(self):
        self.clients: Dict[str, TelegramClient] = {}
        self.running_tasks: Dict[int, asyncio.Task] = {}
        self.stop_events: Dict[int, asyncio.Event] = {}
        self.event_handlers: Dict[int, Tuple[TelegramClient, Any]] = {}
        self._session_locks: Dict[str, asyncio.Lock] = {}
        self.flood_wait_until: Dict[str, datetime] = {}
        self.chat_cooldown_until: Dict[Tuple[str, int], datetime] = {}
        self.user_cooldown_until: Dict[Tuple[int, str], datetime] = {}
        self.worker_stats: Dict[int, Dict[str, Any]] = {}
        self.recent_logs: deque = deque(maxlen=200)

    def _log(self, level: int, msg: str):
        logger.log(level, msg)
        self.recent_logs.append({
            "time": _utcnow().isoformat(),
            "level": logging.getLevelName(level),
            "message": msg,
        })

    @staticmethod
    def _normalize_session_name(session_name: Optional[str]) -> str:
        name = (session_name or "default").strip()
        if name.endswith(".session"):
            name = name[:-8]
        name = os.path.basename(name)
        return name or "default"

    def _get_session_lock(self, session_name: str) -> asyncio.Lock:
        name = self._normalize_session_name(session_name)
        if name not in self._session_locks:
            self._session_locks[name] = asyncio.Lock()
        return self._session_locks[name]

    @staticmethod
    def get_profile_config(profile: Optional[str]) -> Dict[str, Any]:
        return RATE_PROFILES.get(profile or "balanced", RATE_PROFILES["balanced"])

    def _is_stopping(self, campaign_id: Optional[int]) -> bool:
        if campaign_id is None:
            return False
        ev = self.stop_events.get(campaign_id)
        return bool(ev and ev.is_set())

    async def _sleep(self, seconds: float, campaign_id: Optional[int] = None) -> bool:
        """
        Interruptible sleep. Returns True if sleep completed normally,
        or False if interrupted by campaign pause/stop.
        """
        if seconds <= 0:
            return not self._is_stopping(campaign_id)
        if campaign_id is not None and campaign_id in self.stop_events:
            ev = self.stop_events[campaign_id]
            if ev.is_set():
                return False
            try:
                await asyncio.wait_for(ev.wait(), timeout=seconds)
                return False
            except asyncio.TimeoutError:
                return not ev.is_set()
        else:
            await asyncio.sleep(seconds)
            return True

    # ------------------------------------------------------------------
    # Account & Session Management + Reconnection
    # ------------------------------------------------------------------

    async def _sync_account_status(
        self,
        session_name: str,
        status: str,
        phone: Optional[str] = None,
    ):
        """Sync session status with the Account table in the database."""
        from app.database import async_session
        from app.models import Account

        name = self._normalize_session_name(session_name)
        try:
            async with async_session() as session:
                result = await session.execute(
                    select(Account).where(Account.session_name == name)
                )
                account = result.scalar_one_or_none()
                if account:
                    account.status = status
                    if phone and not account.phone:
                        account.phone = phone
                else:
                    account = Account(
                        session_name=name,
                        phone=phone,
                        status=status,
                    )
                    session.add(account)
                await session.commit()
        except Exception as e:
            self._log(logging.WARNING, f"[Account {name}] Failed to sync status '{status}': {e}")

    async def _resolve_campaign_session(
        self,
        campaign_id: int,
        default_session: str = "default",
    ) -> str:
        """Resolve the session_name configured for a campaign (via account_id) or fall back."""
        from app.database import async_session
        from app.models import Campaign, Account

        try:
            async with async_session() as session:
                res = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
                campaign = res.scalar_one_or_none()
                if campaign and campaign.account_id:
                    acc_res = await session.execute(
                        select(Account).where(Account.id == campaign.account_id)
                    )
                    account = acc_res.scalar_one_or_none()
                    if account and account.session_name:
                        return self._normalize_session_name(account.session_name)
        except Exception as e:
            self._log(logging.WARNING, f"[Campaign {campaign_id}] Could not resolve account session: {e}")
        return self._normalize_session_name(default_session)

    async def _connect_with_retry(
        self,
        client: TelegramClient,
        session_name: str,
        campaign_id: Optional[int] = None,
    ):
        """Connect a TelegramClient with exponential backoff retries."""
        last_err = None
        for attempt in range(1, MAX_RECONNECT_ATTEMPTS + 1):
            if self._is_stopping(campaign_id):
                raise asyncio.CancelledError(f"Campaign {campaign_id} stopped during connect")
            try:
                if not client.is_connected():
                    await client.connect()
                if client.is_connected():
                    return
            except FATAL_AUTH_ERRORS as e:
                await self._sync_account_status(session_name, "banned")
                raise PermissionError(f"Account {session_name} is banned or revoked: {e}") from e
            except FloodWaitError as e:
                await self._register_flood_wait(session_name, e.seconds, context="connect")
                waited = await self._wait_out_flood_if_needed(session_name, campaign_id)
                if not waited:
                    raise RuntimeError(
                        f"Account {session_name} in FloodWait ({e.seconds}s) during connect"
                    ) from e
            except Exception as e:
                last_err = e
                backoff = min(RECONNECT_BASE_DELAY * (2 ** (attempt - 1)), 60.0)
                self._log(
                    logging.WARNING,
                    f"[Session {session_name}] Connect attempt {attempt}/{MAX_RECONNECT_ATTEMPTS} "
                    f"failed: {e}. Retrying in {backoff:.1f}s...",
                )
                if campaign_id in self.worker_stats:
                    self.worker_stats[campaign_id]["reconnect_count"] = (
                        self.worker_stats[campaign_id].get("reconnect_count", 0) + 1
                    )
                completed = await self._sleep(backoff, campaign_id)
                if not completed:
                    raise asyncio.CancelledError(f"Campaign {campaign_id} stopped during reconnect backoff")
        raise ConnectionError(
            f"Unable to connect session '{session_name}' after {MAX_RECONNECT_ATTEMPTS} attempts: {last_err}"
        )

    async def get_client(
        self,
        session_name: str,
        phone: Optional[str] = None,
        campaign_id: Optional[int] = None,
    ) -> TelegramClient:
        """Get or initialize an authorized, connected TelegramClient with session locking."""
        name = self._normalize_session_name(session_name)
        lock = self._get_session_lock(name)

        async with lock:
            client = self.clients.get(name)
            if client is not None:
                if not client.is_connected():
                    await self._connect_with_retry(client, name, campaign_id=campaign_id)
            else:
                session_path = os.path.join(SESSIONS_DIR, name)
                client = TelegramClient(
                    session_path,
                    API_ID,
                    API_HASH,
                    connection_retries=MAX_RECONNECT_ATTEMPTS,
                    retry_delay=max(1, int(RECONNECT_BASE_DELAY)),
                    auto_reconnect=True,
                    request_retries=3,
                )
                await self._connect_with_retry(client, name, campaign_id=campaign_id)

            try:
                authorized = await client.is_user_authorized()
            except FATAL_AUTH_ERRORS as e:
                await self._sync_account_status(name, "banned", phone=phone)
                try:
                    await client.disconnect()
                except Exception:
                    pass
                self.clients.pop(name, None)
                raise PermissionError(f"Account {name} deactivated or revoked: {e}") from e

            if not authorized:
                await self._sync_account_status(name, "unauthorized", phone=phone)
                try:
                    await client.disconnect()
                except Exception:
                    pass
                self.clients.pop(name, None)
                raise Exception(f"Account {name} not authorized. Login required first.")

            # Sync account details
            detected_phone = phone
            try:
                me = await client.get_me()
                if me and getattr(me, "phone", None):
                    detected_phone = f"+{me.phone}" if not str(me.phone).startswith("+") else str(me.phone)
            except Exception:
                pass

            self.clients[name] = client
            await self._sync_account_status(name, "active", phone=detected_phone)
            return client

    async def ensure_connected(
        self,
        session_name: str,
        campaign_id: Optional[int] = None,
    ) -> TelegramClient:
        """Ensure the client for session_name is connected and authorized, reconnecting if dropped."""
        name = self._normalize_session_name(session_name)
        client = self.clients.get(name)
        if client is not None and client.is_connected():
            return client
        self._log(logging.INFO, f"[Session {name}] Reconnecting client...")
        return await self.get_client(name, campaign_id=campaign_id)

    async def disconnect_client(self, session_name: str):
        """Cleanly disconnect a specific session client."""
        name = self._normalize_session_name(session_name)
        lock = self._get_session_lock(name)
        async with lock:
            client = self.clients.pop(name, None)
            if client is not None:
                try:
                    await client.disconnect()
                except Exception as e:
                    self._log(logging.WARNING, f"[Session {name}] Error during disconnect: {e}")
                await self._sync_account_status(name, "idle")

    async def disconnect_all(self):
        """Stop all running campaign workers and disconnect all clients cleanly."""
        campaign_ids = list(self.running_tasks.keys())
        for cid in campaign_ids:
            await self.stop_campaign(cid)
        for name in list(self.clients.keys()):
            await self.disconnect_client(name)

    # ------------------------------------------------------------------
    # FloodWait & Rate-Limit Cooldown Management
    # ------------------------------------------------------------------

    async def _register_flood_wait(self, session_name: str, seconds: int, context: str = ""):
        name = self._normalize_session_name(session_name)
        wait_secs = max(1, int(seconds)) + FLOOD_WAIT_BUFFER_SECONDS
        until = _utcnow() + timedelta(seconds=wait_secs)
        prev = self.flood_wait_until.get(name)
        if not prev or until > prev:
            self.flood_wait_until[name] = until
        self._log(
            logging.WARNING,
            f"[Session {name}] FloodWait ({context}): {seconds}s (+{FLOOD_WAIT_BUFFER_SECONDS}s buffer) "
            f"until {self.flood_wait_until[name].isoformat()}",
        )
        await self._sync_account_status(name, "restricted")

    def get_flood_wait_remaining(self, session_name: str) -> float:
        name = self._normalize_session_name(session_name)
        until = self.flood_wait_until.get(name)
        if not until:
            return 0.0
        remaining = (until - _utcnow()).total_seconds()
        if remaining <= 0:
            self.flood_wait_until.pop(name, None)
            return 0.0
        return remaining

    async def _wait_out_flood_if_needed(
        self,
        session_name: str,
        campaign_id: Optional[int] = None,
    ) -> bool:
        """
        If session is in FloodWait:
        - Wait inline if remaining <= MAX_INLINE_FLOOD_WAIT
        - Return False immediately if remaining > MAX_INLINE_FLOOD_WAIT (defer action)
        """
        name = self._normalize_session_name(session_name)
        remaining = self.get_flood_wait_remaining(name)
        if remaining <= 0:
            return True
        if remaining > MAX_INLINE_FLOOD_WAIT:
            self._log(
                logging.WARNING,
                f"[Session {name}] Skipping action: extended FloodWait has {int(remaining)}s remaining "
                f"(> {MAX_INLINE_FLOOD_WAIT}s inline threshold).",
            )
            return False
        self._log(logging.INFO, f"[Session {name}] Waiting {remaining:.1f}s for FloodWait to clear...")
        completed = await self._sleep(remaining, campaign_id)
        if completed and self.get_flood_wait_remaining(name) <= 0:
            await self._sync_account_status(name, "active")
            return True
        return False

    # ------------------------------------------------------------------
    # Daily Caps Enforcement & Daily Report Tracking
    # ------------------------------------------------------------------

    @staticmethod
    def _today_bounds() -> Tuple[str, datetime]:
        now = _utcnow()
        today_str = now.strftime("%Y-%m-%d")
        day_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return today_str, day_start

    async def get_daily_usage(self, campaign_id: int) -> Dict[str, int]:
        """
        Compute today's activity counts for a campaign from the database
        (combining Group, Message, Lead timestamps and today's Report row).
        """
        from app.database import async_session
        from app.models import Group, Message, Lead, Report

        today_str, day_start = self._today_bounds()
        async with async_session() as session:
            joins_q = await session.execute(
                select(func.count(Group.id)).where(
                    Group.campaign_id == campaign_id,
                    Group.joined.is_(True),
                    Group.joined_at >= day_start,
                )
            )
            db_joins = int(joins_q.scalar() or 0)

            sent_q = await session.execute(
                select(func.count(Message.id)).where(
                    Message.campaign_id == campaign_id,
                    Message.direction == "out",
                    Message.created_at >= day_start,
                )
            )
            db_sent = int(sent_q.scalar() or 0)

            recv_q = await session.execute(
                select(func.count(Message.id)).where(
                    Message.campaign_id == campaign_id,
                    Message.direction == "in",
                    Message.created_at >= day_start,
                )
            )
            db_recv = int(recv_q.scalar() or 0)

            leads_q = await session.execute(
                select(func.count(Lead.id)).where(
                    Lead.campaign_id == campaign_id,
                    Lead.created_at >= day_start,
                )
            )
            db_leads = int(leads_q.scalar() or 0)

            rep_q = await session.execute(
                select(Report).where(
                    Report.campaign_id == campaign_id,
                    Report.date == today_str,
                )
            )
            report = rep_q.scalar_one_or_none()

            groups_joined = max(db_joins, int(report.groups_joined or 0)) if report else db_joins
            messages_sent = max(db_sent, int(report.messages_sent or 0)) if report else db_sent
            replies_received = max(db_recv, int(report.replies_received or 0)) if report else db_recv
            leads_captured = max(db_leads, int(report.leads_captured or 0)) if report else db_leads

            return {
                "date": today_str,
                "groups_joined": groups_joined,
                "messages_sent": messages_sent,
                "replies_received": replies_received,
                "leads_captured": leads_captured,
            }

    async def _record_daily_activity(
        self,
        session,
        campaign_id: int,
        groups_joined: int = 0,
        messages_sent: int = 0,
        replies_received: int = 0,
        leads_captured: int = 0,
    ):
        """Upsert today's Report row for campaign_id within the provided DB session."""
        from app.models import Report

        if campaign_id is None:
            return
        today_str, _ = self._today_bounds()
        res = await session.execute(
            select(Report).where(
                Report.campaign_id == campaign_id,
                Report.date == today_str,
            )
        )
        report = res.scalar_one_or_none()
        if not report:
            report = Report(
                campaign_id=campaign_id,
                date=today_str,
                groups_joined=max(0, groups_joined),
                messages_sent=max(0, messages_sent),
                replies_received=max(0, replies_received),
                leads_captured=max(0, leads_captured),
                summary=f"Auto-tracked activity for {today_str}",
            )
            session.add(report)
        else:
            report.groups_joined = int(report.groups_joined or 0) + groups_joined
            report.messages_sent = int(report.messages_sent or 0) + messages_sent
            report.replies_received = int(report.replies_received or 0) + replies_received
            report.leads_captured = int(report.leads_captured or 0) + leads_captured

    async def can_join_group(
        self,
        campaign_id: Optional[int],
        profile: str = "balanced",
    ) -> Tuple[bool, int, int]:
        """Check whether the campaign can join another group today under its rate profile."""
        cfg = self.get_profile_config(profile)
        daily_cap = int(cfg.get("daily_joins", 40))
        if campaign_id is None:
            return True, 0, daily_cap
        usage = await self.get_daily_usage(campaign_id)
        current = usage["groups_joined"]
        return current < daily_cap, current, daily_cap

    async def can_send_message(
        self,
        campaign_id: Optional[int],
        profile: str = "balanced",
    ) -> Tuple[bool, int, int]:
        """Check whether the campaign can send another outgoing message today under its rate profile."""
        cfg = self.get_profile_config(profile)
        daily_cap = int(cfg.get("daily_messages", 150))
        if campaign_id is None:
            return True, 0, daily_cap
        usage = await self.get_daily_usage(campaign_id)
        current = usage["messages_sent"]
        return current < daily_cap, current, daily_cap

    # ------------------------------------------------------------------
    # Group Search & Join
    # ------------------------------------------------------------------

    async def search_and_join_groups(
        self,
        client: TelegramClient,
        keywords: str,
        profile: str,
        campaign_id: int,
        max_joins: Optional[int] = None,
        session_name: str = "default",
    ):
        from app.database import async_session
        from app.models import Group

        results = []
        search_terms = [k.strip() for k in (keywords or "").split(",") if k.strip()]
        if not search_terms:
            return results

        cfg = self.get_profile_config(profile)
        profile_daily_cap = int(cfg.get("daily_joins", 15))
        allowed, current_joins, daily_cap = await self.can_join_group(campaign_id, profile)
        if not allowed:
            self._log(
                logging.INFO,
                f"[Campaign {campaign_id}] Daily join cap already reached ({current_joins}/{daily_cap}).",
            )
            return results

        remaining_today = max(0, daily_cap - current_joins)
        effective_max_joins = (
            min(max_joins, remaining_today)
            if max_joins is not None
            else min(profile_daily_cap, remaining_today)
        )
        if effective_max_joins <= 0:
            return results

        name = self._normalize_session_name(session_name)

        for term in search_terms[:6]:
            if self._is_stopping(campaign_id):
                break

            if not await self._wait_out_flood_if_needed(name, campaign_id):
                break

            try:
                if not client.is_connected():
                    client = await self.ensure_connected(name, campaign_id=campaign_id)

                search = await client(SearchRequest(q=term, limit=10))
                for chat in getattr(search, "chats", []):
                    if self._is_stopping(campaign_id):
                        return results

                    allowed, current_joins, daily_cap = await self.can_join_group(campaign_id, profile)
                    if not allowed or len(results) >= effective_max_joins:
                        self._log(
                            logging.INFO,
                            f"[Campaign {campaign_id}] Reached join limit ({len(results)} joined this run, "
                            f"{current_joins}/{daily_cap} today).",
                        )
                        return results

                    username = getattr(chat, "username", None)
                    chat_id_str = str(getattr(chat, "id", "") or "")
                    title = getattr(chat, "title", "") or username or ""
                    if not username:
                        continue

                    # Check if group already exists in DB to prevent duplicate joins / IntegrityError
                    async with async_session() as session:
                        existing_q = await session.execute(
                            select(Group).where(
                                (Group.telegram_id == chat_id_str) | (Group.username == username)
                            )
                        )
                        existing_group = existing_q.scalars().first()
                        if existing_group and existing_group.joined:
                            continue

                    success, reason = await self.join_group(
                        client,
                        username,
                        profile=profile,
                        session_name=name,
                        campaign_id=campaign_id,
                    )
                    if success:
                        results.append(username)
                        now = _utcnow()
                        async with async_session() as session:
                            try:
                                existing_q = await session.execute(
                                    select(Group).where(
                                        (Group.telegram_id == chat_id_str) | (Group.username == username)
                                    )
                                )
                                grp = existing_q.scalars().first()
                                if grp:
                                    grp.joined = True
                                    grp.joined_at = now
                                    grp.campaign_id = campaign_id
                                    grp.title = title or grp.title
                                    grp.username = username
                                else:
                                    grp = Group(
                                        telegram_id=chat_id_str or username,
                                        title=title,
                                        username=username,
                                        joined=True,
                                        can_send=True,
                                        campaign_id=campaign_id,
                                        joined_at=now,
                                    )
                                    session.add(grp)
                                await self._record_daily_activity(
                                    session, campaign_id, groups_joined=1
                                )
                                await session.commit()
                            except IntegrityError:
                                await session.rollback()
                    else:
                        if reason.startswith("FloodWait") or reason.startswith("PeerFlood"):
                            if not await self._wait_out_flood_if_needed(name, campaign_id):
                                return results

            except FloodWaitError as e:
                await self._register_flood_wait(name, e.seconds, context=f"search:{term}")
                if not await self._wait_out_flood_if_needed(name, campaign_id):
                    break
            except PeerFloodError:
                await self._register_flood_wait(name, PEER_FLOOD_COOLDOWN, context=f"peer_flood:{term}")
                break
            except FATAL_AUTH_ERRORS as e:
                await self._sync_account_status(name, "banned")
                self._log(logging.ERROR, f"[Campaign {campaign_id}] Fatal auth error in search: {e}")
                raise
            except (ConnectionError, OSError) as e:
                self._log(logging.WARNING, f"[Campaign {campaign_id}] Connection lost during search ({term}): {e}")
                try:
                    client = await self.ensure_connected(name, campaign_id=campaign_id)
                except Exception as reconn_err:
                    self._log(logging.ERROR, f"[Campaign {campaign_id}] Reconnect failed: {reconn_err}")
                    break
            except Exception as e:
                self._log(logging.WARNING, f"[Campaign {campaign_id}] Search/join error ({term}): {e}")

        return results

    async def join_group(
        self,
        client: TelegramClient,
        username_or_link: str,
        profile: str = "balanced",
        session_name: str = "default",
        campaign_id: Optional[int] = None,
    ) -> Tuple[bool, str]:
        name = self._normalize_session_name(session_name)

        if self._is_stopping(campaign_id):
            return False, "stopped"

        if campaign_id is not None:
            allowed, current_joins, daily_cap = await self.can_join_group(campaign_id, profile)
            if not allowed:
                return False, f"daily_join_cap_reached ({current_joins}/{daily_cap})"

        if not await self._wait_out_flood_if_needed(name, campaign_id):
            rem = int(self.get_flood_wait_remaining(name))
            return False, f"FloodWait {rem}s"

        delays = self.get_profile_config(profile)["join_delay"]
        delay_secs = random.randint(*delays) if delays[1] > 0 else 0
        if not await self._sleep(delay_secs, campaign_id):
            return False, "stopped"

        try:
            if not client.is_connected():
                client = await self.ensure_connected(name, campaign_id=campaign_id)

            raw = (username_or_link or "").strip()
            is_invite_hash = False
            link = raw
            if "t.me/" in raw:
                tail = raw.split("t.me/")[-1].strip("/")
                if tail.startswith("+") or tail.startswith("joinchat/"):
                    is_invite_hash = True
                    link = tail.lstrip("+").replace("joinchat/", "")
                else:
                    link = tail.split("/")[0]

            if is_invite_hash:
                await client(ImportChatInviteRequest(link))
            else:
                await client(JoinChannelRequest(link))
            return True, "joined"

        except UserAlreadyParticipantError:
            return True, "already_joined"
        except FloodWaitError as e:
            await self._register_flood_wait(name, e.seconds, context=f"join:{username_or_link}")
            await self._wait_out_flood_if_needed(name, campaign_id)
            return False, f"FloodWait {e.seconds}s"
        except PeerFloodError:
            await self._register_flood_wait(name, PEER_FLOOD_COOLDOWN, context="join_peer_flood")
            return False, "PeerFlood"
        except (
            ChannelsTooMuchError,
            UsersTooMuchError,
            InviteHashExpiredError,
            InviteHashInvalidError,
            UsernameInvalidError,
            UsernameNotOccupiedError,
            ChannelPrivateError,
        ) as e:
            self._log(logging.INFO, f"[Session {name}] Cannot join '{username_or_link}': {type(e).__name__}")
            return False, type(e).__name__
        except FATAL_AUTH_ERRORS as e:
            await self._sync_account_status(name, "banned")
            raise PermissionError(f"Account {name} banned/deactivated: {e}") from e
        except (ConnectionError, OSError) as e:
            self._log(logging.WARNING, f"[Session {name}] Disconnected during join_group: {e}")
            try:
                client = await self.ensure_connected(name, campaign_id=campaign_id)
                await client(JoinChannelRequest(username_or_link.split("/")[-1].lstrip("+")))
                return True, "joined_after_reconnect"
            except Exception as retry_err:
                return False, str(retry_err)
        except Exception as e:
            return False, str(e)

    # ------------------------------------------------------------------
    # Sending Messages (Rate-Limited, Reconnect-Safe, FloodWait-Aware)
    # ------------------------------------------------------------------

    async def _mark_group_cannot_send(self, entity: Any):
        from app.database import async_session
        from app.models import Group

        entity_str = str(entity).lstrip("-")
        try:
            async with async_session() as session:
                res = await session.execute(
                    select(Group).where(
                        (Group.telegram_id == str(entity))
                        | (Group.telegram_id == entity_str)
                        | (Group.username == str(entity))
                    )
                )
                for grp in res.scalars().all():
                    grp.can_send = False
                await session.commit()
        except Exception as e:
            self._log(logging.WARNING, f"Failed to mark group {entity} can_send=False: {e}")

    async def send_message(
        self,
        client: TelegramClient,
        entity: Any,
        text: str,
        profile: str = "balanced",
        session_name: str = "default",
        campaign_id: Optional[int] = None,
    ) -> bool:
        name = self._normalize_session_name(session_name)

        if self._is_stopping(campaign_id):
            return False

        if not text or not text.strip():
            return False

        # Enforce daily message cap if campaign_id is provided
        if campaign_id is not None:
            allowed, current_msgs, daily_cap = await self.can_send_message(campaign_id, profile)
            if not allowed:
                self._log(
                    logging.INFO,
                    f"[Campaign {campaign_id}] Daily message cap reached ({current_msgs}/{daily_cap}). Skipping send.",
                )
                return False

        # Check chat-specific SlowMode cooldown
        chat_key = (name, int(entity) if isinstance(entity, int) or str(entity).lstrip("-").isdigit() else hash(str(entity)))
        chat_until = self.chat_cooldown_until.get(chat_key)
        if chat_until and chat_until > _utcnow():
            wait_chat = (chat_until - _utcnow()).total_seconds()
            if wait_chat > MAX_INLINE_FLOOD_WAIT:
                return False
            if not await self._sleep(wait_chat, campaign_id):
                return False

        # Check session-wide FloodWait cooldown
        if not await self._wait_out_flood_if_needed(name, campaign_id):
            return False

        delays = self.get_profile_config(profile)["message_delay"]
        delay_secs = random.randint(*delays) if delays[1] > 0 else 0
        if not await self._sleep(delay_secs, campaign_id):
            return False

        # Re-check daily cap after sleep in case concurrent replies reached the cap
        if campaign_id is not None:
            allowed, current_msgs, daily_cap = await self.can_send_message(campaign_id, profile)
            if not allowed:
                self._log(
                    logging.INFO,
                    f"[Campaign {campaign_id}] Daily message cap reached after delay ({current_msgs}/{daily_cap}).",
                )
                return False

        for attempt in range(1, 3):
            try:
                if not client.is_connected():
                    client = await self.ensure_connected(name, campaign_id=campaign_id)
                await client.send_message(entity, text)
                return True
            except FloodWaitError as e:
                await self._register_flood_wait(name, e.seconds, context=f"send:{entity}")
                if attempt == 1 and await self._wait_out_flood_if_needed(name, campaign_id):
                    continue
                return False
            except SlowModeWaitError as e:
                wait_s = max(1, int(e.seconds)) + 2
                self.chat_cooldown_until[chat_key] = _utcnow() + timedelta(seconds=wait_s)
                self._log(logging.INFO, f"[Session {name}] SlowModeWait ({wait_s}s) in chat {entity}")
                return False
            except PeerFloodError:
                await self._register_flood_wait(name, PEER_FLOOD_COOLDOWN, context="send_peer_flood")
                return False
            except WRITE_FORBIDDEN_ERRORS as e:
                self._log(logging.INFO, f"[Session {name}] Write forbidden in {entity}: {type(e).__name__}")
                await self._mark_group_cannot_send(entity)
                return False
            except FATAL_AUTH_ERRORS as e:
                await self._sync_account_status(name, "banned")
                self._log(logging.ERROR, f"[Session {name}] Fatal auth error on send_message: {e}")
                raise PermissionError(f"Account {name} banned/deactivated: {e}") from e
            except (ConnectionError, OSError) as e:
                self._log(logging.WARNING, f"[Session {name}] Connection error sending message (attempt {attempt}): {e}")
                if attempt == 1:
                    try:
                        client = await self.ensure_connected(name, campaign_id=campaign_id)
                        continue
                    except Exception:
                        return False
                return False
            except RPCError as e:
                self._log(logging.WARNING, f"[Session {name}] Telegram RPC error on send_message: {e}")
                return False
            except Exception as e:
                self._log(logging.WARNING, f"[Session {name}] Unexpected error on send_message: {e}")
                return False
        return False

    # ------------------------------------------------------------------
    # Campaign Worker Loop & Event Handler Lifecycle
    # ------------------------------------------------------------------

    def _remove_campaign_handler(self, campaign_id: int):
        entry = self.event_handlers.pop(campaign_id, None)
        if entry is not None:
            client, handler = entry
            try:
                client.remove_event_handler(handler)
                self._log(logging.INFO, f"[Campaign {campaign_id}] Removed message event handler.")
            except Exception as e:
                self._log(logging.WARNING, f"[Campaign {campaign_id}] Error removing event handler: {e}")

    async def _build_lead_history(self, session, campaign_id: int, lead_id: int, limit: int = 10) -> str:
        from app.models import Message

        res = await session.execute(
            select(Message)
            .where(Message.campaign_id == campaign_id, Message.lead_id == lead_id)
            .order_by(Message.created_at.desc(), Message.id.desc())
            .limit(limit)
        )
        msgs = list(reversed(res.scalars().all()))
        lines = []
        for m in msgs:
            role = "User" if m.direction == "in" else "Assistant"
            lines.append(f"{role}: {m.content}")
        return "\n".join(lines)

    async def run_campaign_worker(self, campaign_id: int, session_name: str = "default"):
        from app.database import async_session
        from app.models import Campaign, Group, Lead, Message

        resolved_session = await self._resolve_campaign_session(campaign_id, default_session=session_name)
        stop_event = self.stop_events.setdefault(campaign_id, asyncio.Event())
        stop_event.clear()

        self.worker_stats[campaign_id] = {
            "campaign_id": campaign_id,
            "session_name": resolved_session,
            "status": "starting",
            "started_at": _utcnow().isoformat(),
            "last_heartbeat": _utcnow().isoformat(),
            "reconnect_count": 0,
            "last_error": None,
        }

        try:
            async with async_session() as session:
                result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
                campaign = result.scalar_one_or_none()
                if not campaign or campaign.status != "running":
                    self.worker_stats[campaign_id]["status"] = "stopped"
                    return
                campaign_keywords = campaign.target_keywords or ""
                campaign_profile = campaign.rate_profile or "balanced"
                campaign_info_bank = campaign.info_bank or "No info bank provided."

            client = await self.get_client(resolved_session, campaign_id=campaign_id)
            self.worker_stats[campaign_id]["status"] = "running"

            # Ensure any stale handler from an earlier run of this campaign is cleaned up first
            self._remove_campaign_handler(campaign_id)

            async def on_new_message(event):
                if self._is_stopping(campaign_id):
                    return
                if getattr(event, "out", False):
                    return

                text = (getattr(event, "raw_text", None) or "").strip()
                if not text:
                    return

                # Check sender (ignore bots)
                sender = getattr(event, "sender", None)
                if sender is None and hasattr(event, "get_sender"):
                    try:
                        sender = await event.get_sender()
                    except Exception:
                        sender = None
                if sender and getattr(sender, "bot", False):
                    return

                sender_id_str = str(getattr(event, "sender_id", None) or getattr(sender, "id", "") or "")
                if not sender_id_str:
                    return

                chat_id = getattr(event, "chat_id", None)
                is_private = bool(getattr(event, "is_private", False))
                group_db_id = None

                # Filter group messages: only respond in campaign groups or keyword-matching discussions
                if not is_private and chat_id is not None:
                    raw_chat_id = str(chat_id).lstrip("-")
                    async with async_session() as sess:
                        grp_res = await sess.execute(
                            select(Group).where(
                                (Group.telegram_id == str(chat_id))
                                | (Group.telegram_id == raw_chat_id)
                            )
                        )
                        grp = grp_res.scalars().first()
                        if grp:
                            if not grp.can_send:
                                return
                            group_db_id = grp.id

                    keywords_list = [
                        k.strip().lower()
                        for k in (campaign_keywords or "").split(",")
                        if k.strip()
                    ]
                    mentioned = bool(getattr(event, "mentioned", False))
                    keyword_hit = any(kw in text.lower() for kw in keywords_list) if keywords_list else True

                    if group_db_id is None and not (mentioned or keyword_hit):
                        return

                    # Enforce per-group anti-spam cooldown
                    if GROUP_REPLY_COOLDOWN > 0:
                        g_key = (resolved_session, int(chat_id) if isinstance(chat_id, int) else hash(str(chat_id)))
                        g_until = self.chat_cooldown_until.get(g_key)
                        if g_until and g_until > _utcnow():
                            return
                        self.chat_cooldown_until[g_key] = _utcnow() + timedelta(
                            seconds=GROUP_REPLY_COOLDOWN
                        )

                # Enforce per-user cooldown so rapid messages don't spawn duplicate replies
                if USER_REPLY_COOLDOWN > 0:
                    u_key = (campaign_id, sender_id_str)
                    u_until = self.user_cooldown_until.get(u_key)
                    if u_until and u_until > _utcnow():
                        return
                    self.user_cooldown_until[u_key] = _utcnow() + timedelta(
                        seconds=USER_REPLY_COOLDOWN
                    )

                # Refresh campaign state from DB
                async with async_session() as sess:
                    c_res = await sess.execute(select(Campaign).where(Campaign.id == campaign_id))
                    curr_campaign = c_res.scalar_one_or_none()
                    if not curr_campaign or curr_campaign.status != "running":
                        return
                    active_profile = curr_campaign.rate_profile or campaign_profile
                    active_info_bank = curr_campaign.info_bank or campaign_info_bank

                    # Deduplicate Lead by (telegram_user_id, campaign_id)
                    l_res = await sess.execute(
                        select(Lead).where(
                            Lead.telegram_user_id == sender_id_str,
                            Lead.campaign_id == campaign_id,
                        )
                    )
                    lead = l_res.scalars().first()
                    detected_lang = detect_language(text)
                    now = _utcnow()
                    is_new_lead = False

                    if not lead:
                        is_new_lead = True
                        lead = Lead(
                            telegram_user_id=sender_id_str,
                            username=getattr(sender, "username", None) if sender else None,
                            first_name=getattr(sender, "first_name", None) if sender else None,
                            language=detected_lang,
                            score="cold",
                            status="new",
                            campaign_id=campaign_id,
                            last_message_at=now,
                        )
                        sess.add(lead)
                        await sess.flush()
                    else:
                        if sender:
                            lead.username = getattr(sender, "username", None) or lead.username
                            lead.first_name = getattr(sender, "first_name", None) or lead.first_name
                        if detected_lang != "auto":
                            lead.language = detected_lang
                        lead.last_message_at = now

                    history_str = await self._build_lead_history(sess, campaign_id, lead.id, limit=10)

                    # Store incoming message immediately
                    in_msg = Message(
                        direction="in",
                        content=text,
                        campaign_id=campaign_id,
                        lead_id=lead.id,
                        group_id=group_db_id,
                    )
                    sess.add(in_msg)
                    await self._record_daily_activity(
                        sess,
                        campaign_id,
                        replies_received=1,
                        leads_captured=1 if is_new_lead else 0,
                    )
                    await sess.commit()
                    lead_id_val = lead.id
                    lead_lang_val = lead.language

                # Check daily message cap before generating/sending outgoing reply
                allowed_send, sent_today, daily_msg_cap = await self.can_send_message(
                    campaign_id, active_profile
                )
                if not allowed_send:
                    self._log(
                        logging.INFO,
                        f"[Campaign {campaign_id}] Recorded incoming message from {sender_id_str}, "
                        f"but daily message cap ({sent_today}/{daily_msg_cap}) is reached.",
                    )
                    return

                reply = await generate_reply(
                    active_info_bank,
                    history_str,
                    text,
                    language=lead_lang_val,
                )
                sent = await self.send_message(
                    client,
                    chat_id,
                    reply,
                    profile=active_profile,
                    session_name=resolved_session,
                    campaign_id=campaign_id,
                )

                if sent:
                    convo_for_scoring = (
                        f"{history_str}\nUser: {text}\nAssistant: {reply}".strip()
                    )
                    new_score = await score_lead(convo_for_scoring)
                    async with async_session() as sess:
                        l_res = await sess.execute(select(Lead).where(Lead.id == lead_id_val))
                        lead_obj = l_res.scalar_one_or_none()
                        if lead_obj:
                            lead_obj.score = new_score
                            if lead_obj.status == "new":
                                lead_obj.status = "contacted"
                            lead_obj.last_message_at = _utcnow()
                        sess.add(
                            Message(
                                direction="out",
                                content=reply,
                                campaign_id=campaign_id,
                                lead_id=lead_id_val,
                                group_id=group_db_id,
                            )
                        )
                        await self._record_daily_activity(
                            sess, campaign_id, messages_sent=1
                        )
                        await sess.commit()

            # Register event handler Before search_and_join_groups so we don't miss messages while joining
            client.add_event_handler(on_new_message, events.NewMessage(incoming=True))
            self.event_handlers[campaign_id] = (client, on_new_message)

            joined = await self.search_and_join_groups(
                client,
                campaign_keywords,
                campaign_profile,
                campaign_id,
                session_name=resolved_session,
            )
            self._log(
                logging.INFO,
                f"[Campaign {campaign_id}] Initial join pass complete: joined {len(joined)} groups: {joined}",
            )
            last_join_date = _utcnow().strftime("%Y-%m-%d")

            # Keep-alive & health check loop while campaign is running
            while not self._is_stopping(campaign_id):
                self.worker_stats[campaign_id]["last_heartbeat"] = _utcnow().isoformat()

                async with async_session() as session:
                    res = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
                    c = res.scalar_one_or_none()
                    if not c or c.status != "running":
                        break
                    campaign_keywords = c.target_keywords or campaign_keywords
                    campaign_profile = c.rate_profile or campaign_profile

                # Verify Telethon client connection health and auto-reconnect if dropped
                if not client.is_connected():
                    self._log(
                        logging.WARNING,
                        f"[Campaign {campaign_id}] Client '{resolved_session}' disconnected. Attempting reconnect...",
                    )
                    client = await self.ensure_connected(resolved_session, campaign_id=campaign_id)
                    # Re-bind handler if a brand new client object was created
                    registered = self.event_handlers.get(campaign_id)
                    if not registered or registered[0] is not client:
                        self._remove_campaign_handler(campaign_id)
                        client.add_event_handler(on_new_message, events.NewMessage(incoming=True))
                        self.event_handlers[campaign_id] = (client, on_new_message)

                # If a new UTC day started and daily join cap has room, run another join pass
                current_date = _utcnow().strftime("%Y-%m-%d")
                if current_date != last_join_date:
                    can_join, _, _ = await self.can_join_group(campaign_id, campaign_profile)
                    if can_join:
                        new_joined = await self.search_and_join_groups(
                            client,
                            campaign_keywords,
                            campaign_profile,
                            campaign_id,
                            session_name=resolved_session,
                        )
                        self._log(
                            logging.INFO,
                            f"[Campaign {campaign_id}] New-day join pass: joined {len(new_joined)} groups",
                        )
                    last_join_date = current_date

                if not await self._sleep(WORKER_HEARTBEAT_SECONDS, campaign_id):
                    break

            self.worker_stats[campaign_id]["status"] = "stopped"

        except asyncio.CancelledError:
            self.worker_stats[campaign_id]["status"] = "stopped"
            self._log(logging.INFO, f"[Campaign {campaign_id}] Worker task cancelled cleanly.")
        except PermissionError as e:
            self.worker_stats[campaign_id]["status"] = "error"
            self.worker_stats[campaign_id]["last_error"] = str(e)
            self._log(logging.ERROR, f"[Campaign {campaign_id}] Authorization/Ban error: {e}")
            # Mark campaign paused in DB so it doesn't stay stuck in 'running'
            try:
                async with async_session() as session:
                    res = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
                    c = res.scalar_one_or_none()
                    if c and c.status == "running":
                        c.status = "paused"
                        await session.commit()
            except Exception:
                pass
        except Exception as e:
            self.worker_stats[campaign_id]["status"] = "error"
            self.worker_stats[campaign_id]["last_error"] = str(e)
            self._log(logging.ERROR, f"[Campaign {campaign_id}] Worker error: {e}")
        finally:
            self._remove_campaign_handler(campaign_id)

    def start_campaign(self, campaign_id: int, session_name: str = "default") -> asyncio.Task:
        existing = self.running_tasks.get(campaign_id)
        if existing is not None and not existing.done():
            return existing
        stop_ev = self.stop_events.setdefault(campaign_id, asyncio.Event())
        stop_ev.clear()
        task = asyncio.create_task(self.run_campaign_worker(campaign_id, session_name))
        self.running_tasks[campaign_id] = task
        return task

    async def pause_campaign(self, campaign_id: int):
        """Signal campaign worker to stop immediately and remove its event listener."""
        ev = self.stop_events.get(campaign_id)
        if ev:
            ev.set()
        self._remove_campaign_handler(campaign_id)
        task = self.running_tasks.get(campaign_id)
        if task and not task.done():
            try:
                await asyncio.wait_for(asyncio.shield(task), timeout=2.0)
            except asyncio.TimeoutError:
                task.cancel()
                try:
                    await task
                except (asyncio.CancelledError, Exception):
                    pass
        if campaign_id in self.worker_stats:
            self.worker_stats[campaign_id]["status"] = "paused"

    async def stop_campaign(self, campaign_id: int):
        """Stop campaign worker and clean up handlers."""
        await self.pause_campaign(campaign_id)
        if campaign_id in self.worker_stats:
            self.worker_stats[campaign_id]["status"] = "stopped"

    def get_worker_status(self, campaign_id: Optional[int] = None) -> Dict[str, Any]:
        if campaign_id is not None:
            stats = dict(self.worker_stats.get(campaign_id, {}))
            task = self.running_tasks.get(campaign_id)
            stats["is_active"] = bool(task and not task.done())
            session_name = stats.get("session_name", "default")
            stats["flood_wait_remaining"] = round(self.get_flood_wait_remaining(session_name), 1)
            return stats

        return {
            cid: self.get_worker_status(cid)
            for cid in set(list(self.worker_stats.keys()) + list(self.running_tasks.keys()))
        }


telegram_manager = TelegramManager()
