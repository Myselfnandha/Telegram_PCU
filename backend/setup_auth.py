#!/usr/bin/env python3
"""
Interactive CLI Setup Script for TG Power Suite Authentication.
Generates and verifies the Telethon .session file for the application.
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
    print("   TG POWER SUITE - CLI TELEGRAM AUTHENTICATION SETUP")
    print("=" * 65)

    api_id_str = os.getenv("TG_API_ID", os.getenv("API_ID", "")).strip()
    api_hash = os.getenv("TG_API_HASH", os.getenv("API_HASH", "")).strip()
    session_name = os.getenv("TG_SESSION_NAME", "tg_uploader").strip()
    phone = os.getenv("TG_PHONE", os.getenv("PHONE_NUMBER", "")).strip()
    session_file = SESSION_DIR / session_name

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

    session_full_path = Path(f"{session_file}.session")
    if session_full_path.exists():
        try:
            os.chmod(session_full_path, 0o600)
            os.chmod(SESSION_DIR, 0o700)
        except Exception:
            pass

    client = TelegramClient(str(session_file), api_id, api_hash)
    await client.connect()

    if await client.is_user_authorized():
        me = await client.get_me()
        print("\n" + "=" * 65)
        print(" [SUCCESS] Already Authenticated!")
        print(f" User: {me.first_name} {me.last_name or ''}")
        print(f" Username: @{me.username or 'N/A'}")
        print(f" User ID: {me.id}")
        print(f" Session Path: {session_file}.session")
        print("=" * 65)

        switch_choice = input("\nDo you want to switch account or re-authenticate? (y/N): ").strip().lower()
        if switch_choice != 'y':
            print("\n[+] Keeping active session. Start TG Power Suite with: python backend/run.py\n")
            await client.disconnect()
            return
        else:
            print("\n[!] Logging out of existing session...")
            await client.log_out()
            await client.disconnect()
            await client.connect()

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
    print("\nYou can now start the application by running: python backend/run.py\n")

    # Secure session permissions
    if session_full_path.exists():
        try:
            os.chmod(session_full_path, 0o600)
            os.chmod(SESSION_DIR, 0o700)
        except Exception:
            pass

    # Sync .env
    env_path = SCRIPT_DIR / ".env"
    if not env_path.exists():
        env_path = ROOT_DIR / ".env"

    env_lines = []
    if env_path.exists():
        with open(env_path, "r") as f:
            env_lines = f.readlines()

    keys_to_set = {
        "TG_API_ID": str(api_id),
        "TG_API_HASH": api_hash,
        "TG_PHONE": phone,
        "TG_SESSION_NAME": session_name,
        "HOST": "0.0.0.0",
        "PORT": "8088"
    }

    new_lines = []
    seen_keys = set()
    for line in env_lines:
        line_clean = line.strip()
        if "=" in line_clean and not line_clean.startswith("#"):
            k, _ = line_clean.split("=", 1)
            k = k.strip()
            if k in keys_to_set:
                new_lines.append(f"{k}={keys_to_set[k]}\n")
                seen_keys.add(k)
                continue
        new_lines.append(line)

    for k, v in keys_to_set.items():
        if k not in seen_keys:
            new_lines.append(f"{k}={v}\n")

    with open(env_path, "w") as f:
        f.writelines(new_lines)
    print(f"[+] Saved credentials and settings to {env_path}")

    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
