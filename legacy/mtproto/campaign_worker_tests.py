import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch, AsyncMock

# Use an isolated temporary database for tests before importing app modules
_tmp_dir = tempfile.mkdtemp(prefix="tg_campaign_test_")
os.environ["API_ID"] = "12345"
os.environ["API_HASH"] = "test_api_hash_12345"

import app.config as config

config.DATA_DIR = _tmp_dir
config.SESSIONS_DIR = os.path.join(_tmp_dir, "sessions")
config.LOGS_DIR = os.path.join(_tmp_dir, "logs")
config.DATABASE_URL = f"sqlite+aiosqlite:///{os.path.join(_tmp_dir, 'test_campaigns.db')}"
config.RECONNECT_BASE_DELAY = 0.05
config.FLOOD_WAIT_BUFFER_SECONDS = 0
config.MAX_INLINE_FLOOD_WAIT = 2
config.USER_REPLY_COOLDOWN = 0
config.GROUP_REPLY_COOLDOWN = 0
config.WORKER_HEARTBEAT_SECONDS = 0.1

from sqlalchemy import select, delete
from telethon.errors import (
    FloodWaitError,
    SlowModeWaitError,
    PeerFloodError,
    ChatWriteForbiddenError,
    UserAlreadyParticipantError,
    UserDeactivatedBanError,
)
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.contacts import SearchRequest

import app.database as db
from app.models import Account, Campaign, Group, Lead, Message, Report
import app.telegram_manager as tm_mod
from app.telegram_manager import TelegramManager, detect_language

# Patch module-level constants in telegram_manager for fast unit tests
tm_mod.SESSIONS_DIR = config.SESSIONS_DIR
tm_mod.RECONNECT_BASE_DELAY = 0.05
tm_mod.FLOOD_WAIT_BUFFER_SECONDS = 0
tm_mod.MAX_INLINE_FLOOD_WAIT = 1
tm_mod.PEER_FLOOD_COOLDOWN = 120
tm_mod.USER_REPLY_COOLDOWN = 0
tm_mod.GROUP_REPLY_COOLDOWN = 0
tm_mod.WORKER_HEARTBEAT_SECONDS = 0.05
tm_mod.RATE_PROFILES = {
    "safe": {
        "join_delay": (0, 0),
        "message_delay": (0, 0),
        "daily_joins": 2,
        "daily_messages": 3,
    },
    "balanced": {
        "join_delay": (0, 0),
        "message_delay": (0, 0),
        "daily_joins": 4,
        "daily_messages": 5,
    },
    "aggressive": {
        "join_delay": (0, 0),
        "message_delay": (0, 0),
        "daily_joins": 10,
        "daily_messages": 15,
    },
}


class FakeChat:
    def __init__(self, chat_id, username, title="Test Group"):
        self.id = chat_id
        self.username = username
        self.title = title


class FakeSearchResult:
    def __init__(self, chats):
        self.chats = chats


class FakeUser:
    def __init__(self, user_id=999, username="rahul_k", first_name="Rahul", phone="919876543210", bot=False):
        self.id = user_id
        self.username = username
        self.first_name = first_name
        self.phone = phone
        self.bot = bot


class FakeEvent:
    def __init__(
        self,
        raw_text,
        sender_id=1001,
        chat_id=1001,
        is_private=True,
        out=False,
        sender=None,
        mentioned=False,
    ):
        self.raw_text = raw_text
        self.sender_id = sender_id
        self.chat_id = chat_id
        self.is_private = is_private
        self.out = out
        self.sender = sender or FakeUser(user_id=sender_id)
        self.mentioned = mentioned


class FakeTelegramClient:
    def __init__(self, session_path, api_id, api_hash, **kwargs):
        self.session_path = session_path
        self.api_id = api_id
        self.api_hash = api_hash
        self.connected = False
        self.authorized = True
        self.connect_failures_remaining = 0
        self.fatal_error_on_auth = None
        self.handlers = []
        self.sent_messages = []
        self.joined_channels = []
        self.search_results = []
        self.join_side_effect = None
        self.send_side_effect = None
        self.me = FakeUser(user_id=777, username="campaign_owner", first_name="Owner", phone="919999999999")

    async def connect(self):
        if self.connect_failures_remaining > 0:
            self.connect_failures_remaining -= 1
            raise ConnectionError("Simulated network drop during connect")
        self.connected = True

    def is_connected(self):
        return self.connected

    async def disconnect(self):
        self.connected = False

    async def is_user_authorized(self):
        if self.fatal_error_on_auth:
            raise self.fatal_error_on_auth
        return self.authorized

    async def get_me(self):
        return self.me

    def add_event_handler(self, callback, event_builder=None):
        self.handlers.append(callback)

    def remove_event_handler(self, callback, event_builder=None):
        if callback in self.handlers:
            self.handlers.remove(callback)
            return 1
        return 0

    async def send_message(self, entity, text):
        if not self.connected:
            raise ConnectionError("Client disconnected before send_message")
        if self.send_side_effect:
            eff = self.send_side_effect
            if isinstance(eff, list):
                if eff:
                    item = eff.pop(0)
                    if isinstance(item, Exception):
                        raise item
            elif isinstance(eff, Exception):
                raise eff
        self.sent_messages.append((entity, text))
        return True

    async def __call__(self, request):
        if not self.connected:
            raise ConnectionError("Client disconnected before RPC request")
        if isinstance(request, SearchRequest):
            return FakeSearchResult(self.search_results)
        if isinstance(request, JoinChannelRequest):
            if self.join_side_effect:
                eff = self.join_side_effect
                if isinstance(eff, list) and eff:
                    item = eff.pop(0)
                    if isinstance(item, Exception):
                        raise item
                elif isinstance(eff, Exception):
                    raise eff
            self.joined_channels.append(request.channel)
            return True
        return True


def make_flood_wait(seconds: int) -> FloodWaitError:
    err = FloodWaitError(request=None, capture=seconds)
    err.seconds = seconds
    return err


def make_slow_mode(seconds: int) -> SlowModeWaitError:
    err = SlowModeWaitError(request=None, capture=seconds)
    err.seconds = seconds
    return err


class TestHardenedCampaignWorker(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        await db.init_db()
        async with db.async_session() as session:
            for model in (Message, Lead, Group, Report, Campaign, Account):
                await session.execute(delete(model))
            await session.commit()
        self.manager = TelegramManager()

    async def asyncTearDown(self):
        await self.manager.disconnect_all()

    async def test_01_language_detection(self):
        self.assertEqual(detect_language("नमस्ते मुझे जानकारी चाहिए"), "hi")
        self.assertEqual(detect_language("Bhai iska price kitna hai?"), "hinglish")
        self.assertEqual(detect_language("Hello, what are the pricing plans?"), "en")
        self.assertEqual(detect_language("   "), "auto")

    @patch("app.telegram_manager.TelegramClient", side_effect=FakeTelegramClient)
    async def test_02_session_management_and_reconnection(self, mock_client_cls):
        # 1. Connect normal authorized client and verify Account table sync
        client = await self.manager.get_client("acc_one")
        self.assertTrue(client.is_connected())
        async with db.async_session() as session:
            acc = (await session.execute(select(Account).where(Account.session_name == "acc_one"))).scalar_one()
            self.assertEqual(acc.status, "active")
            self.assertEqual(acc.phone, "+919999999999")

        # 2. Simulate dropped connection + transient failures before reconnecting
        client.connected = False
        client.connect_failures_remaining = 2
        reconnected = await self.manager.ensure_connected("acc_one")
        self.assertIs(reconnected, client)
        self.assertTrue(reconnected.is_connected())
        self.assertEqual(client.connect_failures_remaining, 0)

        # 3. Unauthorized session handling
        unauth_client = FakeTelegramClient("unauth", 12345, "hash")
        unauth_client.authorized = False
        with patch("app.telegram_manager.TelegramClient", return_value=unauth_client):
            with self.assertRaises(Exception) as ctx:
                await self.manager.get_client("unauth_sess")
            self.assertIn("not authorized", str(ctx.exception))

        async with db.async_session() as session:
            acc_u = (await session.execute(select(Account).where(Account.session_name == "unauth_sess"))).scalar_one()
            self.assertEqual(acc_u.status, "unauthorized")

        # 4. Banned account handling
        banned_client = FakeTelegramClient("banned", 12345, "hash")
        banned_client.fatal_error_on_auth = UserDeactivatedBanError(request=None)
        with patch("app.telegram_manager.TelegramClient", return_value=banned_client):
            with self.assertRaises(PermissionError):
                await self.manager.get_client("banned_sess")

        async with db.async_session() as session:
            acc_b = (await session.execute(select(Account).where(Account.session_name == "banned_sess"))).scalar_one()
            self.assertEqual(acc_b.status, "banned")

    @patch("app.telegram_manager.TelegramClient", side_effect=FakeTelegramClient)
    async def test_03_flood_wait_slow_mode_and_forbidden_handling(self, mock_client_cls):
        client = await self.manager.get_client("flood_sess")

        # Create campaign and group
        async with db.async_session() as session:
            camp = Campaign(name="Flood Test", objective="Test", info_bank="Info", target_keywords="test", rate_profile="balanced", status="running")
            session.add(camp)
            await session.flush()
            grp = Group(telegram_id="555001", title="ReadOnly Group", username="readonly_grp", joined=True, can_send=True, campaign_id=camp.id)
            session.add(grp)
            await session.commit()
            camp_id = camp.id

        # 1. Short FloodWait (1s <= MAX_INLINE_FLOOD_WAIT): should wait inline and retry send_message
        client.send_side_effect = [make_flood_wait(1)]
        ok = await self.manager.send_message(client, 555001, "Hello after short flood", "balanced", session_name="flood_sess", campaign_id=camp_id)
        self.assertTrue(ok)
        self.assertEqual(len(client.sent_messages), 1)

        # 2. Extended FloodWait (60s > MAX_INLINE_FLOOD_WAIT): should register cooldown and return False immediately
        client.send_side_effect = [make_flood_wait(60)]
        ok2 = await self.manager.send_message(client, 555001, "Blocked by long flood", "balanced", session_name="flood_sess", campaign_id=camp_id)
        self.assertFalse(ok2)
        self.assertGreater(self.manager.get_flood_wait_remaining("flood_sess"), 30)

        # Clear flood wait to test SlowModeWaitError and ChatWriteForbiddenError
        self.manager.flood_wait_until.clear()

        # 3. SlowModeWaitError: sets chat cooldown
        client.send_side_effect = [make_slow_mode(30)]
        ok3 = await self.manager.send_message(client, 555001, "Slow mode msg", "balanced", session_name="flood_sess", campaign_id=camp_id)
        self.assertFalse(ok3)
        # Immediate retry to same chat should be skipped while other chats can still receive messages
        ok3_retry = await self.manager.send_message(client, 555001, "Slow mode msg 2", "balanced", session_name="flood_sess", campaign_id=camp_id)
        self.assertFalse(ok3_retry)
        ok_other_chat = await self.manager.send_message(client, 999888, "Other chat msg", "balanced", session_name="flood_sess", campaign_id=camp_id)
        self.assertTrue(ok_other_chat)

        # 4. ChatWriteForbiddenError: marks Group.can_send = False in DB
        self.manager.chat_cooldown_until.clear()
        client.send_side_effect = [ChatWriteForbiddenError(request=None)]
        ok4 = await self.manager.send_message(client, 555001, "Forbidden msg", "balanced", session_name="flood_sess", campaign_id=camp_id)
        self.assertFalse(ok4)
        async with db.async_session() as session:
            grp_after = (await session.execute(select(Group).where(Group.telegram_id == "555001"))).scalar_one()
            self.assertFalse(grp_after.can_send)

    @patch("app.telegram_manager.TelegramClient", side_effect=FakeTelegramClient)
    async def test_04_daily_caps_enforcement_and_group_deduplication(self, mock_client_cls):
        client = await self.manager.get_client("caps_sess")
        client.search_results = [
            FakeChat(101, "edu_group_1", "Edu 1"),
            FakeChat(102, "edu_group_2", "Edu 2"),
            FakeChat(103, "edu_group_3", "Edu 3"),
            FakeChat(104, "edu_group_4", "Edu 4"),
        ]

        async with db.async_session() as session:
            # 'safe' profile has daily_joins=2, daily_messages=3 in test config
            camp = Campaign(
                name="Safe Cap Campaign",
                objective="Test caps",
                info_bank="Coaching info",
                target_keywords="education,coaching",
                rate_profile="safe",
                status="running",
            )
            session.add(camp)
            await session.commit()
            camp_id = camp.id

        # 1. Search and join should stop at daily_joins == 2
        joined = await self.manager.search_and_join_groups(
            client, "education,coaching", "safe", camp_id, session_name="caps_sess"
        )
        self.assertEqual(len(joined), 2)
        self.assertEqual(joined, ["edu_group_1", "edu_group_2"])

        can_join, used_joins, max_joins = await self.manager.can_join_group(camp_id, "safe")
        self.assertFalse(can_join)
        self.assertEqual(used_joins, 2)
        self.assertEqual(max_joins, 2)

        # Attempting another search_and_join_groups on the same day should join 0 groups
        joined_again = await self.manager.search_and_join_groups(
            client, "education", "safe", camp_id, session_name="caps_sess"
        )
        self.assertEqual(joined_again, [])

        # 2. Test daily_messages cap (3 messages allowed in 'safe' profile)
        with patch("app.telegram_manager.generate_reply", new=AsyncMock(return_value="AI Reply")), \
             patch("app.telegram_manager.score_lead", new=AsyncMock(return_value="warm")):
            self.manager.start_campaign(camp_id, session_name="caps_sess")
            await asyncio.sleep(0.1)
            self.assertEqual(len(client.handlers), 1)
            handler = client.handlers[0]

            # Send 4 incoming messages from the same user
            for i in range(1, 5):
                ev = FakeEvent(f"Bhai coaching fees kitna hai #{i}?", sender_id=4242, chat_id=4242, is_private=True)
                await handler(ev)

            await self.manager.pause_campaign(camp_id)

        async with db.async_session() as session:
            leads = (await session.execute(select(Lead).where(Lead.campaign_id == camp_id))).scalars().all()
            self.assertEqual(len(leads), 1)
            self.assertEqual(leads[0].language, "hinglish")
            self.assertEqual(leads[0].score, "warm")
            self.assertEqual(leads[0].status, "contacted")

            in_msgs = (await session.execute(
                select(Message).where(Message.campaign_id == camp_id, Message.direction == "in")
            )).scalars().all()
            out_msgs = (await session.execute(
                select(Message).where(Message.campaign_id == camp_id, Message.direction == "out")
            )).scalars().all()
            self.assertEqual(len(in_msgs), 4)
            self.assertEqual(len(out_msgs), 3)

            rep = (await session.execute(select(Report).where(Report.campaign_id == camp_id))).scalar_one()
            self.assertEqual(rep.groups_joined, 2)
            self.assertEqual(rep.messages_sent, 3)
            self.assertEqual(rep.replies_received, 4)
            self.assertEqual(rep.leads_captured, 1)

    @patch("app.telegram_manager.TelegramClient", side_effect=FakeTelegramClient)
    async def test_05_handler_lifecycle_no_duplicates_on_restart(self, mock_client_cls):
        client = await self.manager.get_client("lifecycle_sess")

        async with db.async_session() as session:
            camp = Campaign(
                name="Lifecycle Campaign",
                objective="Test handler cleanup",
                info_bank="Info",
                target_keywords="python",
                rate_profile="balanced",
                status="running",
            )
            session.add(camp)
            await session.commit()
            camp_id = camp.id

        # Start -> Pause -> Restart -> Stop
        self.manager.start_campaign(camp_id, session_name="lifecycle_sess")
        await asyncio.sleep(0.1)
        self.assertEqual(len(client.handlers), 1)

        await self.manager.pause_campaign(camp_id)
        self.assertEqual(len(client.handlers), 0)

        # Restart campaign
        self.manager.start_campaign(camp_id, session_name="lifecycle_sess")
        await asyncio.sleep(0.1)
        self.assertEqual(len(client.handlers), 1)

        await self.manager.stop_campaign(camp_id)
        self.assertEqual(len(client.handlers), 0)

    async def test_06_fastapi_endpoints(self):
        from httpx import AsyncClient, ASGITransport
        from app.main import app

        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as ac:
            # Create campaign
            res = await ac.post("/api/campaigns", json={
                "name": "API Test Campaign",
                "objective": "Generate leads",
                "info_bank": "We offer Python bootcamp at Rs 999",
                "target_keywords": "python,coding",
                "rate_profile": "balanced",
            })
            self.assertEqual(res.status_code, 200)
            cid = res.json()["id"]

            # List campaigns
            list_res = await ac.get("/api/campaigns")
            self.assertEqual(list_res.status_code, 200)
            camps = list_res.json()
            self.assertEqual(len(camps), 1)
            self.assertIn("daily_usage", camps[0])
            self.assertIn("daily_caps", camps[0])

            # Check usage endpoint
            usage_res = await ac.get(f"/api/campaigns/{cid}/usage")
            self.assertEqual(usage_res.status_code, 200)
            self.assertEqual(usage_res.json()["caps"]["daily_joins"], 4)
            self.assertEqual(usage_res.json()["caps"]["daily_messages"], 5)

            # Check status & logs endpoints
            st_res = await ac.get("/api/status")
            self.assertEqual(st_res.status_code, 200)
            self.assertEqual(st_res.json()["status"], "running")

            logs_res = await ac.get("/api/logs")
            self.assertEqual(logs_res.status_code, 200)
            self.assertIn("logs", logs_res.json())

    @patch("app.telegram_manager.TelegramClient", side_effect=FakeTelegramClient)
    async def test_07_invite_links_peer_flood_and_heartbeat_reconnect(self, mock_client_cls):
        client = await self.manager.get_client("edge_sess")

        # 1. Join invite link (https://t.me/+AbCdEf123)
        ok_invite, status_invite = await self.manager.join_group(
            client, "https://t.me/+AbCdEf123", "balanced", session_name="edge_sess"
        )
        self.assertTrue(ok_invite)
        self.assertEqual(status_invite, "joined")

        # 2. UserAlreadyParticipantError treated as success ('already_joined')
        client.join_side_effect = [UserAlreadyParticipantError(request=None)]
        ok_already, status_already = await self.manager.join_group(
            client, "existing_channel", "balanced", session_name="edge_sess"
        )
        self.assertTrue(ok_already)
        self.assertEqual(status_already, "already_joined")

        # 3. PeerFloodError triggers protective cooldown and restricts account
        client.join_side_effect = [PeerFloodError(request=None)]
        ok_pf, status_pf = await self.manager.join_group(
            client, "spam_channel", "balanced", session_name="edge_sess"
        )
        self.assertFalse(ok_pf)
        self.assertEqual(status_pf, "PeerFlood")
        self.assertGreater(self.manager.get_flood_wait_remaining("edge_sess"), 60)
        self.manager.flood_wait_until.clear()

        # 4. Worker heartbeat auto-reconnects when connection drops mid-campaign
        async with db.async_session() as session:
            acc = (await session.execute(select(Account).where(Account.session_name == "edge_sess"))).scalar_one()
            camp = Campaign(
                name="Heartbeat Reconnect Campaign",
                objective="Test auto-reconnect",
                info_bank="Info",
                target_keywords="ai",
                rate_profile="balanced",
                status="running",
                account_id=acc.id,
            )
            session.add(camp)
            await session.commit()
            camp_id = camp.id

        self.manager.start_campaign(camp_id, session_name="default")
        await asyncio.sleep(0.08)
        # Drop connection while worker is running and require 1 retry before reconnect succeeds
        client.connected = False
        client.connect_failures_remaining = 1
        await asyncio.sleep(0.2)
        self.assertTrue(client.is_connected())
        self.assertGreaterEqual(self.manager.worker_stats[camp_id]["reconnect_count"], 1)
        await self.manager.stop_campaign(camp_id)

    @patch("app.telegram_manager.TelegramClient", side_effect=FakeTelegramClient)
    async def test_08_group_filtering_and_multi_turn_history(self, mock_client_cls):
        client = await self.manager.get_client("filter_sess")

        async with db.async_session() as session:
            camp = Campaign(
                name="Filter & History Campaign",
                objective="Sell course",
                info_bank="Course fee is Rs 500",
                target_keywords="python,bootcamp",
                rate_profile="aggressive",
                status="running",
            )
            session.add(camp)
            await session.commit()
            camp_id = camp.id

        captured_histories = []

        async def fake_generate_reply(info_bank, history, user_message, language="auto"):
            captured_histories.append(history)
            return f"Reply to: {user_message}"

        with patch("app.telegram_manager.generate_reply", side_effect=fake_generate_reply), \
             patch("app.telegram_manager.score_lead", new=AsyncMock(return_value="hot")):
            self.manager.start_campaign(camp_id, session_name="filter_sess")
            await asyncio.sleep(0.08)
            handler = client.handlers[0]

            # A. Ignore message from a bot
            bot_user = FakeUser(user_id=888, username="some_bot", bot=True)
            await handler(FakeEvent("Hello I am a bot", sender_id=888, chat_id=888, is_private=True, sender=bot_user))
            self.assertEqual(len(client.sent_messages), 0)

            # B. Ignore message in unrelated group without keyword match
            await handler(FakeEvent("Good morning everyone", sender_id=2001, chat_id=-100999, is_private=False))
            self.assertEqual(len(client.sent_messages), 0)

            # C. Reply to message in non-joined group when it matches campaign keyword ("python")
            await handler(FakeEvent("Anyone knows a good python course?", sender_id=2002, chat_id=-100888, is_private=False))
            self.assertEqual(len(client.sent_messages), 1)
            self.assertEqual(captured_histories[-1], "")

            # D. Second message from same user 2002 in DM -> receives multi-turn conversation history!
            await handler(FakeEvent("How do I enroll?", sender_id=2002, chat_id=2002, is_private=True))
            self.assertEqual(len(client.sent_messages), 2)
            self.assertIn("User: Anyone knows a good python course?", captured_histories[-1])
            self.assertIn("Assistant: Reply to: Anyone knows a good python course?", captured_histories[-1])

            await self.manager.stop_campaign(camp_id)


if __name__ == "__main__":
    unittest.main()
