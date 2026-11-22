#!/usr/bin/env python3
import asyncio
import argparse
import json
import sys
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from app.db.session import get_sessionmaker, init_db, close_db
from app.db.repositories.user_repo import UserRepository
from app.telegram.session_migration import LegacySessionMigrator


async def main():
    parser = argparse.ArgumentParser(description="TG Power Suite v3.0 — Legacy Session Migration Tool")
    parser.add_argument("--inspect", action="store_true", help="Inspect disk for legacy SQLite session files and exit")
    parser.add_argument("--owner-id", type=str, help="UUID of the designated application user in app_users")
    parser.add_argument("--primary-tg-id", type=int, help="Telegram numeric user ID for the primary account")
    parser.add_argument("--secondary-tg-id", type=int, help="Telegram numeric user ID for secondary Turbo account")
    parser.add_argument("--dry-run", action="store_true", help="Perform extraction and validation without database writes")
    parser.add_argument("--create-admin", action="store_true", help="Create a designated admin user if owner-id does not exist")
    parser.add_argument("--admin-name", type=str, default="Root Admin", help="Name for the newly created admin user")

    args = parser.parse_args()

    if args.inspect:
        metadata = LegacySessionMigrator.inspect_legacy_sessions()
        print(json.dumps(metadata, indent=2))
        return 0

    if not args.owner_id and not args.create_admin:
        print("Error: Either --owner-id or --create-admin must be specified.", file=sys.stderr)
        return 1

    if not args.primary_tg_id:
        print("Error: --primary-tg-id must be specified.", file=sys.stderr)
        return 1

    await init_db()
    session_factory = get_sessionmaker()

    async with session_factory() as session:
        user_repo = UserRepository(session)
        owner_uuid = None

        if args.owner_id:
            try:
                owner_uuid = uuid.UUID(args.owner_id)
            except ValueError:
                print(f"Error: Invalid UUID format for --owner-id: {args.owner_id}", file=sys.stderr)
                return 1

        if args.create_admin and (not owner_uuid or not await user_repo.get_by_id(owner_uuid)):
            owner_uuid = owner_uuid or uuid.uuid4()
            admin_user = await user_repo.create_user(
                display_name=args.admin_name,
                role="admin",
                status="active",
                user_id=owner_uuid
            )
            await session.commit()
            print(f"Created designated admin user: {admin_user.display_name} (ID: {admin_user.id})")

        migrator = LegacySessionMigrator(session)
        result = await migrator.migrate(
            owner_user_id=owner_uuid,
            primary_telegram_user_id=args.primary_tg_id,
            secondary_telegram_user_id=args.secondary_tg_id,
            dry_run=args.dry_run
        )

        print("\n=== Migration Result ===")
        print(f"Success: {result.success}")
        print(f"Dry Run: {result.dry_run}")
        print(f"Owner UUID: {result.owner_user_id}")
        if result.migrated_accounts:
            print("Migrated Accounts:")
            for acc in result.migrated_accounts:
                print(f"  - Role: {acc.get('role')}, TG ID: {acc.get('telegram_user_id')}, Status: {acc.get('status')}")
        if result.errors:
            print("Errors:")
            for err in result.errors:
                print(f"  - {err}")
        if result.notes:
            print("Notes:")
            for note in result.notes:
                print(f"  - {note}")

    await close_db()
    return 0 if result.success else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
