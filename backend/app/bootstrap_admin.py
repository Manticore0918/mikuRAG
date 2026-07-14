import argparse
import asyncio
import getpass
import os

from sqlalchemy import exists, select

from app.database import session_factory
from app.models import User
from app.schemas import UsernamePassword
from app.security import hash_password


def read_password() -> str:
    environment_password = os.getenv("MIKURAG_BOOTSTRAP_PASSWORD")
    if environment_password:
        return environment_password
    password = getpass.getpass("Administrator password: ")
    confirmation = getpass.getpass("Confirm password: ")
    if password != confirmation:
        raise SystemExit("Passwords do not match")
    return password


async def create_first_administrator(username: str, password: str) -> None:
    credentials = UsernamePassword(username=username, password=password)
    async with session_factory() as session:
        administrator_exists = await session.scalar(
            select(exists().where(User.is_administrator.is_(True)))
        )
        if administrator_exists:
            raise SystemExit("An Administrator already exists; use the Administrator interface")
        session.add(
            User(
                username=credentials.username,
                password_hash=hash_password(credentials.password),
                is_administrator=True,
                is_enabled=True,
            )
        )
        await session.commit()
    print(f"Administrator '{credentials.username}' created")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create the first mikuRAG Administrator")
    parser.add_argument("--username", required=True)
    args = parser.parse_args()
    asyncio.run(create_first_administrator(args.username, read_password()))


if __name__ == "__main__":
    main()
