"""Developer CLI.

Run:  uv run python -m chmabapay.cli bootstrap
      uv run python -m chmabapay.cli set-password <email>
      uv run python -m chmabapay.cli grant-admin <email> [--password <pw>] [--name <name>]
      uv run python -m chmabapay.cli telegram-chat-id
Env:  WEBHOOK_SINK_URL=http://localhost:9000/hook   (optional, bootstrap)
      CHMABAPAY_PASSWORD=...                       (optional, non-interactive set-password
                                                    and grant-admin)
"""

from __future__ import annotations

import asyncio
import getpass
import os
import sys
from datetime import UTC, datetime

from sqlalchemy import func, select

from . import models
from .db import run_migrations, seed_plans_if_needed, session_factory
from .routers.auth import _ensure_free_subscription
from .schemas import LinkIn, StoreCreate
from .security import hash_key, hash_password, new_api_key, new_secret
from .services import stores as store_svc


def _parse_grant_admin_args(argv: list[str]) -> tuple[list[str], dict[str, str]]:
    """Split `grant-admin` arguments into positionals and `--flag value` options.

    Hand-rolled because the alternative is pulling argparse into a CLI that has
    three commands. Both `--name value` and `--name=value` are accepted. A flag
    with nothing after it is refused rather than treated as absent, because
    `--password` followed by nothing would otherwise create an admin with no
    password and report success.
    """
    positional: list[str] = []
    options: dict[str, str] = {}
    index = 0
    while index < len(argv):
        arg = argv[index]
        if arg.startswith("--"):
            key, _, inline = arg[2:].partition("=")
            if inline:
                options[key] = inline
                index += 1
                continue
            if index + 1 >= len(argv):
                raise SystemExit(f"--{key} needs a value")
            options[key] = argv[index + 1]
            index += 2
            continue
        positional.append(arg)
        index += 1
    return positional, options




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


def _chats_in_updates(updates: list[dict]) -> list[tuple[str, str, str]]:
    """(chat id, type, label) for every distinct chat in a getUpdates payload.

    Distinct because one busy group produces many updates and the answer an operator
    wants is the short list of places the bot can deliver to. `my_chat_member` is
    included alongside messages: it is the update Telegram sends when the bot is added
    to a group, which is exactly the moment the id becomes discoverable.
    """
    found: dict[str, tuple[str, str, str]] = {}
    for update in updates:
        if not isinstance(update, dict):
            continue
        for key in ("message", "edited_message", "channel_post", "my_chat_member"):
            item = update.get(key)
            chat = item.get("chat") if isinstance(item, dict) else None
            if not isinstance(chat, dict) or chat.get("id") is None:
                continue
            chat_id = str(chat["id"])
            label = (
                chat.get("title")
                or " ".join(
                    part
                    for part in (chat.get("first_name"), chat.get("last_name"))
                    if part
                )
                or chat.get("username")
                or "(no name)"
            )
            found.setdefault(chat_id, (chat_id, str(chat.get("type") or "unknown"), label))
    return sorted(found.values(), key=lambda row: row[0])


async def _telegram_chat_id() -> None:
    """Print the chat ids the bot can deliver to.

    Why this exists: `ACTIVITY_TELEGRAM_CHAT_ID` is a numeric id, a bot cannot be
    added to a group by invite link and cannot open a conversation, and Telegram has no
    API that turns `t.me/+…` into an id. So the operator invites the bot, says
    something in the group, and runs this. The alternative is guessing against
    `{"ok": false}`, which arrives with HTTP 200 and looks like success.
    """
    from .services import telegram

    try:
        updates = await telegram.get_updates()
    except telegram.TelegramError as exc:
        raise SystemExit(f"could not read Telegram updates: {exc}") from exc

    chats = _chats_in_updates(updates)
    if not chats:
        print("Telegram has no chats to report yet.")
        print("  A group only appears here once the bot is a member AND a message has")
        print("  been sent in it (a bot cannot open a conversation, and cannot join")
        print("  from an invite link on its own). Add the bot, post something in the")
        print("  group, then run this again.")
        return

    print("Chats this bot can deliver to:\n")
    for chat_id, kind, label in chats:
        print(f"  {chat_id:<22} {kind:<12} {label}")
    print("\nPut the group's id in the deployment's environment as:")
    print("  ACTIVITY_TELEGRAM_CHAT_ID=<id>")


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


async def _grant_admin() -> None:
    """Create or promote a platform admin, optionally with a password.

    Without this, the only way to become an admin was a Google sign-in from an
    address listed in `CHMABAPAY_ADMIN_EMAILS` — and the console refuses SSO
    sessions, so that sign-in could not open the console. `set-password` could
    not close the gap either: it exits when the account does not exist yet, and
    it never grants `is_platform_admin`. Day-0 therefore meant: sign in with
    Google, then SSH in, then run the CLI. This collapses it to one command, and
    it works on a fresh database with no OAuth round trip at all.
    """
    positional, options = _parse_grant_admin_args(sys.argv[2:])
    if not positional:
        raise SystemExit(
            "usage: python -m chmabapay.cli grant-admin <email> "
            "[--password <pw>] [--name <name>]"
        )
    email = positional[0].strip().lower()
    if "@" not in email:
        raise SystemExit(f"{email!r} is not an email address")

    name = options.get("name") or email.split("@")[0]
    password = options.get("password") or os.getenv("CHMABAPAY_PASSWORD") or ""

    await run_migrations()
    await seed_plans_if_needed()

    async with session_factory() as session:
        account = (
            await session.execute(
                select(models.Account).where(func.lower(models.Account.email) == email)
            )
        ).scalar_one_or_none()
        created = account is None
        if account is None:
            account = models.Account(
                email=email,
                name=name,
                is_platform_admin=True,
                whitelabel_enabled=True,
            )
            session.add(account)
            await session.flush()
            # A new account with no subscription has no billing row, which would
            # leave the console's own account unable to hold an invoice — and the
            # HQ store that self-pay billing charges against lives on it.
            await _ensure_free_subscription(session, account)
        else:
            account.is_platform_admin = True
            account.whitelabel_enabled = True
            account.updated_at = datetime.now(UTC)
            session.add(account)

        if password:
            account.password_hash = hash_password(password)

        await session.commit()
        await session.refresh(account)

    verb = "created" if created else "promoted"
    print(f"admin {verb}: {account.email} (account id={account.id})")
    print(f"  is_platform_admin={account.is_platform_admin}")
    print(f"  password={'set' if password else 'unchanged'}")
    if not password:
        print(
            "\nNo password was supplied, so the console cannot be opened yet.\n"
            "Re-run with --password <pw> (or set CHMABAPAY_PASSWORD) to set one."
        )


def main() -> None:
    command = sys.argv[1] if len(sys.argv) > 1 else "bootstrap"
    if command == "bootstrap":
        asyncio.run(_bootstrap())
        return
    if command == "set-password":
        asyncio.run(_set_password())
        return
    if command == "grant-admin":
        asyncio.run(_grant_admin())
        return
    if command == "telegram-chat-id":
        asyncio.run(_telegram_chat_id())
        return
    raise SystemExit(
        f"unknown command {command!r} — expected 'bootstrap', 'set-password', "
        "'grant-admin' or 'telegram-chat-id'"
    )


if __name__ == "__main__":
    main()
