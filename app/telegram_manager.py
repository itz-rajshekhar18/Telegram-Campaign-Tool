import asyncio
import random
from datetime import datetime
from telethon import TelegramClient, events
from telethon.tl.functions.channels import JoinChannelRequest
from telethon.tl.functions.contacts import SearchRequest
from telethon.errors import FloodWaitError
from sqlalchemy import select
from app.config import API_ID, API_HASH, RATE_PROFILES, SESSIONS_DIR
from app.ai_engine import generate_reply, score_lead
import os

os.makedirs(SESSIONS_DIR, exist_ok=True)

class TelegramManager:
    def __init__(self):
        self.clients = {}
        self.running_tasks = {}

    async def get_client(self, session_name: str, phone: str = None):
        if session_name in self.clients:
            client = self.clients[session_name]
            if client.is_connected():
                return client
        client = TelegramClient(f"{SESSIONS_DIR}/{session_name}", API_ID, API_HASH)
        await client.connect()
        if not await client.is_user_authorized():
            raise Exception(f"Account {session_name} not authorized. Login required first.")
        self.clients[session_name] = client
        return client

    async def search_and_join_groups(self, client, keywords: str, profile: str, campaign_id: int, max_joins: int = 15):
        from app.database import async_session
        from app.models import Group
        results = []
        search_terms = [k.strip() for k in (keywords or "").split(",") if k.strip()]
        
        for term in search_terms[:4]:
            try:
                search = await client(SearchRequest(q=term, limit=8))
                for chat in getattr(search, 'chats', []):
                    username = getattr(chat, 'username', None)
                    if not username:
                        continue
                    success, _ = await self.join_group(client, username, profile)
                    if success:
                        results.append(username)
                        async with async_session() as session:
                            g = Group(
                                telegram_id=str(getattr(chat, 'id', '')),
                                title=getattr(chat, 'title', ''),
                                username=username,
                                joined=True,
                                campaign_id=campaign_id,
                                joined_at=datetime.utcnow()
                            )
                            session.add(g)
                            await session.commit()
                    if len(results) >= max_joins:
                        return results
            except FloodWaitError as e:
                await asyncio.sleep(e.seconds + 20)
            except Exception as e:
                print(f"Search/join error ({term}): {e}")
        return results

    async def join_group(self, client, username_or_link: str, profile: str = "balanced"):
        delays = RATE_PROFILES.get(profile, RATE_PROFILES["balanced"])["join_delay"]
        await asyncio.sleep(random.randint(*delays))
        try:
            link = username_or_link
            if link.startswith("https://t.me/"):
                link = link.split("/")[-1].lstrip("+")
            await client(JoinChannelRequest(link))
            return True, "joined"
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 10)
            return False, f"FloodWait {e.seconds}s"
        except Exception as e:
            return False, str(e)

    async def send_message(self, client, entity, text: str, profile: str = "balanced"):
        delays = RATE_PROFILES.get(profile, RATE_PROFILES["balanced"])["message_delay"]
        await asyncio.sleep(random.randint(*delays))
        try:
            await client.send_message(entity, text)
            return True
        except FloodWaitError as e:
            await asyncio.sleep(e.seconds + 5)
            return False
        except Exception:
            return False

    async def run_campaign_worker(self, campaign_id: int, session_name: str = "default"):
        from app.database import async_session
        from app.models import Campaign, Lead, Message
        
        try:
            async with async_session() as session:
                result = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
                campaign = result.scalar_one_or_none()
                if not campaign or campaign.status != "running":
                    return

            client = await self.get_client(session_name)
            
            joined = await self.search_and_join_groups(
                client, campaign.target_keywords, campaign.rate_profile, campaign_id
            )
            print(f"[Campaign {campaign_id}] Joined {len(joined)} groups: {joined}")

            @client.on(events.NewMessage(incoming=True))
            async def on_new_message(event):
                text = event.raw_text or ""
                if not text.strip():
                    return
                # Only respond in groups or private for now
                reply = await generate_reply(campaign.info_bank or "No info bank provided.", "", text)
                sent = await self.send_message(client, event.chat_id, reply, campaign.rate_profile)
                
                if sent:
                    async with async_session() as sess:
                        lead = Lead(
                            telegram_user_id=str(event.sender_id or ""),
                            username=getattr(event.sender, "username", None) if event.sender else None,
                            first_name=getattr(event.sender, "first_name", None) if event.sender else None,
                            campaign_id=campaign_id,
                            score=await score_lead(text + "\n" + reply),
                            last_message_at=datetime.utcnow()
                        )
                        sess.add(lead)
                        await sess.flush()
                        sess.add(Message(direction="in", content=text, campaign_id=campaign_id, lead_id=lead.id))
                        sess.add(Message(direction="out", content=reply, campaign_id=campaign_id, lead_id=lead.id))
                        await sess.commit()

            # Keep alive while campaign is running
            while True:
                async with async_session() as session:
                    res = await session.execute(select(Campaign).where(Campaign.id == campaign_id))
                    c = res.scalar_one_or_none()
                    if not c or c.status != "running":
                        break
                await asyncio.sleep(20)
                
        except Exception as e:
            print(f"[Campaign {campaign_id}] Worker error: {e}")

    def start_campaign(self, campaign_id: int, session_name: str = "default"):
        if campaign_id not in self.running_tasks or self.running_tasks[campaign_id].done():
            task = asyncio.create_task(self.run_campaign_worker(campaign_id, session_name))
            self.running_tasks[campaign_id] = task

telegram_manager = TelegramManager()
