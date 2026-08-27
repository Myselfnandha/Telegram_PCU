#!/usr/bin/env python3
"""
Interactive CLI Setup Script for Telegram MTProto Authentication.
Generates and verifies the Telethon .session file for the web application.
"""

import os
import sys
import asyncio
from pathlib import Path
from dotenv import load_dotenv

# Setup paths
SCRIPT_DIR = Path(__file__).resolve().parent
ROOT_DIR = SCRIPT_DIR.parent
SESSION_DIR = SCRIPT_DIR / "sessions"
SESSION_DIR.mkdir(parents=True, exist_ok=True)

# Load env
load_dotenv(SCRIPT_DIR / ".env")
load_dotenv(ROOT_DIR / ".env")

try:
    from telethon import TelegramClient
    from telethon.errors import (
        SessionPasswordNeededError,
        PhoneNumberInvalidError,
        PhoneCodeInvalidError,
        PhoneCodeExpiredError
    )
except ImportError:
    print("[ERROR] Telethon is not installed! Please run: pip install -r requirements.txt")
    sys.exit(1)


async def main():
    print("=" * 65)
    print("   TELEGRAM WEB UPLOADER - ONE-TIME CLI AUTHENTICATION SETUP")
    print("=" * 65)

    api_id_str = os.getenv("TG_API_ID", "").strip()
    api_hash = os.getenv("TG_API_HASH", "").strip()
    session_name = os.getenv("TG_SESSION_NAME", "tg_uploader").strip()
    phone = os.getenv("TG_PHONE", "").strip()

    if not api_id_str or not api_hash:
        print("\n[!] Telegram API Credentials not found in environment.")
        print("    Get your API ID and Hash from https://my.telegram.org (under API Development Tools)\n")
        
        while not api_id_str:
            api_id_str = input("Enter your TG_API_ID (integer): ").strip()
        while not api_hash:
            api_hash = input("Enter your TG_API_HASH (string): ").strip()

    try:
        api_id = int(api_id_str)
    except ValueError:
        print("[ERROR] TG_API_ID must be a valid integer.")
        sys.exit(1)

    session_file = SESSION_DIR / session_name
    print(f"\n[+] Using session path: {session_file}.session")

    client = TelegramClient(str(session_file), api_id, api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print("\n[SUCCESS] Already authenticated!")
        print(f"Logged in as: {me.first_name} {me.last_name or ''} (@{me.username or 'No username'}) [ID: {me.id}]")
        await client.disconnect()
        return

    if not phone:
        phone = input("\nEnter your Telegram phone number (with country code, e.g. +1234567890): ").strip()

    print(f"\n[+] Sending login verification code to {phone} via Telegram...")
    try:
        sent_code = await client.send_code_request(phone)
    except PhoneNumberInvalidError:
        print("[ERROR] The phone number is invalid. Make sure to include the country code.")
        await client.disconnect()
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Failed to send code: {e}")
        await client.disconnect()
        sys.exit(1)

    code = input("Enter the Telegram verification code you received: ").strip()

    try:
        await client.sign_in(phone, code)
    except SessionPasswordNeededError:
        print("\n[!] Two-Step Verification (2FA) is enabled on this account.")
        import getpass
        password = getpass.getpass("Enter your 2FA Password: ")
        try:
            await client.sign_in(password=password)
        except Exception as e:
            print(f"[ERROR] 2FA Sign-in failed: {e}")
            await client.disconnect()
            sys.exit(1)
    except PhoneCodeInvalidError:
        print("[ERROR] The code you entered is incorrect.")
        await client.disconnect()
        sys.exit(1)
    except PhoneCodeExpiredError:
        print("[ERROR] The code has expired. Please run the script again.")
        await client.disconnect()
        sys.exit(1)
    except Exception as e:
        print(f"[ERROR] Sign-in error: {e}")
        await client.disconnect()
        sys.exit(1)

    me = await client.get_me()
    print("\n" + "=" * 65)
    print(f" [SUCCESS] Authenticated successfully!")
    print(f" User: {me.first_name} {me.last_name or ''}")
    print(f" Username: @{me.username or 'N/A'}")
    print(f" User ID: {me.id}")
    print(f" Session file saved: {session_file}.session")
    print("=" * 65)
    print("\nYou can now start the web application by running: python run.py\n")

    # Update .env if values were entered manually and .env doesn't have them
    env_path = SCRIPT_DIR / ".env"
    if not env_path.exists():
        with open(env_path, "w") as f:
            f.write(f"TG_API_ID={api_id}\n")
            f.write(f"TG_API_HASH={api_hash}\n")
            f.write(f"TG_PHONE={phone}\n")
            f.write(f"TG_SESSION_NAME={session_name}\n")
            f.write("HOST=0.0.0.0\nPORT=8000\n")
        print(f"[+] Created default .env file at {env_path}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
