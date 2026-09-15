"""Safely bootstrap or promote one administrator account."""

import argparse
import asyncio
import getpass
import os
from datetime import UTC, datetime

from sqlalchemy import select

from app.common.security import hash_password
from app.config import get_settings
from app.database import create_engine, create_session_factory
from app.models import AuditLog, User


async def create_or_promote(email: str, password: str | None) -> None:
    """Create an administrator or promote an existing account."""
    settings = get_settings()
    engine = create_engine(settings)
    factory = create_session_factory(engine)
    normalized_email = email.strip().casefold()
    async with factory() as session:
        user = await session.scalar(select(User).where(User.email == normalized_email))
        action = "bootstrap_admin_promoted"
        if user is None:
            if not password:
                raise ValueError("A password is required when creating an administrator")
            character_classes = (
                any(character.islower() for character in password),
                any(character.isupper() for character in password),
                any(character.isdigit() for character in password),
            )
            if len(password) < 12 or len(password) > 128 or sum(character_classes) < 2:
                raise ValueError(
                    "Password must be 12-128 characters with two character classes"
                )
            user = User(
                email=normalized_email,
                first_name="Platform",
                last_name="Administrator",
                password_hash=hash_password(password),
                status="ACTIVE",
                role="ADMIN",
                onboarding_completed=False,
            )
            session.add(user)
            await session.flush()
            action = "bootstrap_admin_created"
        else:
            user.role = "ADMIN"
            user.status = "ACTIVE"
        session.add(
            AuditLog(
                actor_user_id=user.id,
                action=action,
                resource_type="user",
                resource_id=str(user.id),
                metadata_payload={"email": normalized_email},
                created_at=datetime.now(UTC),
            )
        )
        await session.commit()
    await engine.dispose()


def main() -> None:
    """Read password without command-line exposure and run the bootstrap."""
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    args = parser.parse_args()
    password = os.getenv("BOOTSTRAP_ADMIN_PASSWORD")
    if password is None:
        password = getpass.getpass(
            "New password (leave blank when promoting an existing user): "
        )
    asyncio.run(create_or_promote(args.email, password or None))


if __name__ == "__main__":
    main()
