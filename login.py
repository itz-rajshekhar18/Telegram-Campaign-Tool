"""
Easy Telegram account login helper.
Run this once to authorize your account and save the session.
"""
import asyncio
from telethon import TelegramClient
from app.config import API_ID, API_HASH, SESSIONS_DIR
import os

os.makedirs(SESSIONS_DIR, exist_ok=True)

async def login():
    print("=== Telegram Account Login ===")
    print("This will create a session file so the tool can use your account.\n")
    
    phone = input("Enter phone number (with country code, e.g. +91xxxxxxxxxx): ").strip()
    if not phone:
        print("Phone number required.")
        return
    
    session_name = input("Session name (press Enter for 'default'): ").strip() or "default"
    session_path = f"{SESSIONS_DIR}/{session_name}"
    
    client = TelegramClient(session_path, API_ID, API_HASH)
    await client.connect()
    
    if await client.is_user_authorized():
        me = await client.get_me()
        print(f"\nAlready logged in as: @{me.username or me.first_name} (ID: {me.id})")
        await client.disconnect()
        return
    
    print(f"\nSending code to {phone}...")
    await client.send_code_request(phone)
    
    code = input("Enter the code you received on Telegram: ").strip()
    try:
        await client.sign_in(phone, code)
    except Exception as e:
        # 2FA password if needed
        if "password" in str(e).lower() or "two" in str(e).lower():
            password = input("Two-step verification password: ").strip()
            await client.sign_in(password=password)
        else:
            print(f"Login error: {e}")
            await client.disconnect()
            return
    
    me = await client.get_me()
    print(f"\nSuccess! Logged in as: @{me.username or 'no-username'} ({me.first_name})")
    print(f"Session saved as: sessions/{session_name}.session")
    print("You can now start the main tool.")
    await client.disconnect()

if __name__ == "__main__":
    if API_ID == 0 or not API_HASH:
        print("ERROR: Please set API_ID and API_HASH in the .env file first.")
        print("Get them from https://my.telegram.org")
    else:
        asyncio.run(login())
