"""Developer CLI.

Run:  uv run python -m chmabapay.cli bootstrap
      uv run python -m chmabapay.cli set-password <email>
Env:  WEBHOOK_SINK_URL=http://localhost:9000/hook   (optional, bootstrap)
      CHMABAPAY_PASSWORD=...                       (optional, non-interactive set-password)
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys

from sqlalchemy import func, select

from . import models
from .db import run_migrations, seed_plans_if_needed, session_factory
from .schemas import LinkIn, StoreCreate
from .security import hash_key, hash_password, new_api_key, new_secret
from .services import stores as store_svc


async def _bootstrap() -> None:
    # The CLI is the setup entry point, so it is the one place that applies
    # migrations. The app itself only ever verifies.
    await run_migrations()
    await seed_plans_if_needed()

    sink_url = os.getenv("WEBHOOK_SINK_URL", "").strip()

    async with session_factory() as session:
        account = (
            await session.execute(
                select(models.Account).where(models.Account.email == "admin@chmaba.test")
            )
        ).scalar_one_or_none()
        if account is None:
            account = models.Account(email="admin@chmaba.test", name="Chmaba POS")
            session.add(account)
            await session.commit()
            await session.refresh(account)
        print(f"account id={account.id} name={account.name}")

        def mk_store(name: str, owner: str, account_id: int):
            slug = f"{owner.replace(' ', '').lower()}-{name.replace(' ', '').lower()}"
            return StoreCreate(
                name=name,
                owner_name=owner,
                owner_email=f"{owner.replace(' ', '.').lower()}@example.com",
                external_id=f"ext_{slug}",
                link=LinkIn(
                    raw_link=f"https://link.payway.com.kh/{slug}",
                    merchant_account_id=slug,
                    merchant_name=name,
                ),
            )

        stores = {}
        for label, name, owner in (("A", "Demo Store A", "Sokha A"), ("B", "Demo Store B", "Dara B")):
            existing = (
                await session.execute(
                    select(models.Store).where(models.Store.name == name)
                )
            ).scalar_one_or_none()
            if existing is None:
                store = await store_svc.create_store(session, account, mk_store(name, owner, account.id))
                stores[label] = store
                print(f"  store {label}: id={store.public_id} status={store.status} "
                      f"link={store.public_id}")
            else:
                stores[label] = existing
                print(f"  store {label}: id={existing.public_id} (exists)")

        prefix, raw_account_key = new_api_key()
        account_key = models.ApiKey(
            account_id=account.id,
            scope=models.KEY_ACCOUNT_SCOPE,
            key_prefix=prefix,
            key_hash=hash_key(raw_account_key),
        )
        session.add(account_key)

        if sink_url:
            endpoint = (
                await session.execute(
                    select(models.WebhookEndpoint).where(models.WebhookEndpoint.url == sink_url)
                )
            ).scalar_one_or_none()
            if endpoint is None:
                endpoint = models.WebhookEndpoint(
                    account_id=account.id,
                    url=sink_url,
                    secret_key=new_secret(),
                )
                session.add(endpoint)
            print(f"  webhook (shared): url={sink_url} secret={endpoint.secret_key}")
        else:
            endpoint = None

        await session.commit()

    print("\nWORKSPACE API KEY (one key for every store in this account):")
    print("  " + raw_account_key)
    if endpoint is not None:
        print(f"\nWEBHOOK SIGNING SECRET: {endpoint.secret_key}")

    print("\nTry it:")
    print(f"  curl -X POST http://127.0.0.1:8000/v1/payments -H 'Authorization: Bearer {raw_account_key}' "
          f"-H 'Content-Type: application/json' -d '{{\"amount\": 1.50, \"reference_id\": \"order_1\", "
          f"\"store\": \"{next(iter(stores.values())).public_id}\"}}'")
    print("  POST /_dev/payments/<id>/pay  -> simulate the customer paying")


async def _set_password() -> None:
    """Set or reset an account's password for email + password sign-in."""
    if len(sys.argv) < 3:
        raise SystemExit("usage: python -m chmabapay.cli set-password <email>")
    email = sys.argv[2].strip().lower()

    password = os.getenv("CHMABAPAY_PASSWORD") or ""
    if password:
        print("using CHMABAPAY_PASSWORD from the environment")
    else:
        password = getpass.getpass("New password: ")
        if password != getpass.getpass("Repeat password: "):
            raise SystemExit("passwords do not match")
    try:
        password_hash = hash_password(password)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    await run_migrations()
    async with session_factory() as session:
        account = (
            await session.execute(
                select(models.Account).where(
                    func.lower(models.Account.email) == email
                )
            )
        ).scalar_one_or_none()
        if account is None:
            raise SystemExit(f"no account with email {email}")
        account.password_hash = password_hash
        session.add(account)
        await session.commit()
        print(f"password set for {account.email} (account id={account.id})")


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "bootstrap"
    if command == "bootstrap":
        asyncio.run(_bootstrap())
        return
    if command == "set-password":
        asyncio.run(_set_password())
        return
    raise SystemExit(
        f"unknown command {command!r} — expected 'bootstrap' or 'set-password'"
    )


if __name__ == "__main__":
    main()
