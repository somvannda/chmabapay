import json
from datetime import UTC

import httpx
from conftest import make_account, make_key, make_store, make_webhook

from chmabapay import models, webhooks
from chmabapay.db import session_factory
from chmabapay.security import verify_signature


async def test_expired_qr_is_no_longer_served_and_reissue_mints_a_successor(client):
    """ABA owns the ~180s window, so a dead code is retired and replaced.

    Uses the offline builder (`hosted_qr: false`) so the test exercises our own
    state machine instead of spending a live ABA checkout.
    """
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from chmabapay.services.payments import expire_due_payments

    account = await make_account()
    await make_store(account, name="Sokha Cafe", owner="Sokha")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 4.0, "reference_id": "order_7", "hosted_qr": False},
            headers=headers,
        )
    ).json()
    assert created["status"] == "pending"

    # while payable, the code is served and is scalable
    live_qr = await client.get(f"/pay/{created['id']}/qr.svg")
    assert live_qr.status_code == 200
    assert live_qr.headers["content-type"].startswith("image/svg+xml")
    assert "viewBox" in live_qr.text

    # a code that still has time on it is not replaceable
    early = await client.post(f"/v1/payments/{created['id']}/reissue", headers=headers)
    assert early.status_code == 409
    assert early.json()["detail"] == "payment_not_expired"

    # the window elapses
    async with session_factory() as session:
        await session.execute(
            update(models.Payment)
            .where(models.Payment.public_id == created["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        expired = await expire_due_payments(session)
        assert any(p.public_id == created["id"] for p in expired)

    # the dead code is withdrawn rather than redrawn
    dead = await client.get(f"/pay/{created['id']}/qr.svg")
    assert dead.status_code == 410
    assert dead.json()["detail"] == "payment_expired"

    # one merchant action mints the replacement
    minted = await client.post(f"/v1/payments/{created['id']}/reissue", headers=headers)
    assert minted.status_code == 201
    successor = minted.json()
    assert successor["id"] != created["id"]
    assert successor["status"] == "pending"
    assert successor["amount"] == created["amount"]
    assert successor["reference_id"] == "order_7"
    assert successor["reissued_from"] == created["id"]
    assert (await client.get(f"/pay/{successor['id']}/qr.svg")).status_code == 200

    # the parent is left behind as the record of the session that expired
    parent = (
        await client.get(f"/v1/payments/{created['id']}", headers=headers)
    ).json()
    assert parent["status"] == "expired"

    # pressing the button again reuses the live code instead of stacking ABA
    # sessions for one sale
    replay = await client.post(f"/v1/payments/{created['id']}/reissue", headers=headers)
    assert replay.status_code == 200
    assert replay.json()["id"] == successor["id"]

    # the successor can itself be replaced once its own window lapses
    async with session_factory() as session:
        await session.execute(
            update(models.Payment)
            .where(models.Payment.public_id == successor["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        await expire_due_payments(session)
    third = await client.post(f"/v1/payments/{successor['id']}/reissue", headers=headers)
    assert third.status_code == 201
    assert third.json()["id"] not in (created["id"], successor["id"])


async def test_reissue_refuses_paid_payment_and_another_tenant(client):
    from datetime import datetime, timedelta

    from sqlalchemy import update

    from chmabapay.services.payments import expire_due_payments

    account = await make_account()
    await make_store(account, name="Sokha Cafe", owner="Sokha")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 6.0, "hosted_qr": False},
            headers=headers,
        )
    ).json()

    async with session_factory() as session:
        await session.execute(
            update(models.Payment)
            .where(models.Payment.public_id == created["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        await expire_due_payments(session)

    # a settled payment is not a candidate for replacement
    assert (await client.post(f"/_dev/payments/{created['id']}/pay")).status_code == 200
    paid = await client.post(f"/v1/payments/{created['id']}/reissue", headers=headers)
    assert paid.status_code == 409
    assert paid.json()["detail"] == "payment_already_paid"

    # another tenant cannot spend a session on someone else's payment
    other = await make_account(email="other@chmaba.test", name="Other")
    other_raw, _ = await make_key(other)
    cross = await client.post(
        f"/v1/payments/{created['id']}/reissue",
        headers={"Authorization": f"Bearer {other_raw}"},
    )
    assert cross.status_code == 404


async def test_offline_qr_is_refused_when_nothing_can_confirm_it(client, monkeypatch):
    """A live offline QR with no confirmation source is unpayable by design.

    There is no ABA session to ask and no Bakong credentials to match against,
    so the row could never leave `pending`. Refusing beats handing out a code
    that cannot settle.
    """
    from chmabapay.config import Settings
    from chmabapay.routers import payments as payments_router

    account = await make_account()
    await make_store(account, name="Sokha Cafe", owner="Sokha")
    live_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {live_key}"}

    # A production-shaped deployment: no dev rail, no Bakong credentials.
    monkeypatch.setattr(
        payments_router,
        "get_settings",
        lambda: Settings(
            enable_dev_gateway=False,
            bakong_api_token=None,
            bakong_developer_email=None,
        ),
    )

    refused = await client.post(
        "/v1/payments", json={"amount": 1.0, "hosted_qr": False}, headers=headers
    )
    assert refused.status_code == 400
    assert refused.json()["detail"].startswith("offline_qr_requires_a_confirmation_source")

    # A test-mode key is settled by W1's test-mode bypass, which needs no ledger.
    test_key, _ = await make_key(account, mode="test")
    allowed = await client.post(
        "/v1/payments",
        json={"amount": 1.0, "hosted_qr": False},
        headers={"Authorization": f"Bearer {test_key}"},
    )
    assert allowed.status_code == 201

    # Omitting hosted_qr must never trip the guard, because ABA issues the code
    # and answers for it. It runs for real here, so it fails at ABA instead —
    # what matters is which failure.
    auto = await client.post("/v1/payments", json={"amount": 1.0}, headers=headers)
    assert "offline_qr_requires_a_confirmation_source" not in auto.text


async def test_account_key_payment_flow_plus_shared_webhook(client, monkeypatch):
    account = await make_account()
    store_a = await make_store(
        account, name="Sokha Cafe", owner="Sokha", external_id="sokha-ext-1"
    )
    raw_key, _ = await make_key(account)
    # one shared, account-level webhook endpoint
    await make_webhook(account, url="https://sink.example.com/shared")

    captured: list[tuple[str, bytes, dict]] = []

    async def fake_post(url, payload: bytes, headers: dict):
        captured.append((url, payload, headers))
        return httpx.Response(200, content=b"ok", request=httpx.Request("POST", url))

    monkeypatch.setattr(webhooks, "http_post", fake_post)

    headers = {"Authorization": f"Bearer {raw_key}"}
    # create for store A
    body = {"amount": 1.5, "reference_id": "order_1024", "store": store_a.public_id}
    r = await client.post("/v1/payments", json=body, headers=headers)
    assert r.status_code == 201
    created = r.json()
    assert created["status"] == "pending"
    assert created["amount"] == "1.50"
    assert created["qr_string"]
    assert created["checkout_url"].endswith(f"/pay/{created['id']}")
    assert created["store"] == store_a.public_id
    assert created["external_id"] == "sokha-ext-1"

    # idempotent replay
    r2 = await client.post(
        "/v1/payments",
        json={**body, "idempotency_key": "dup-1"},
        headers=headers,
    )
    assert r2.status_code == 201
    r3 = await client.post(
        "/v1/payments",
        json={**body, "idempotency_key": "dup-1"},
        headers=headers,
    )
    assert r3.status_code == 200
    assert r2.json()["id"] == r3.json()["id"]

    payment_id = r2.json()["id"]

    # list + single get
    lst = (await client.get(f"/v1/payments?store={store_a.public_id}", headers=headers)).json()
    assert [p["id"] for p in lst["data"]] == [payment_id, created["id"]]

    # simulate the rail reporting the credit
    dev = await client.post(f"/_dev/payments/{payment_id}/pay")
    assert dev.status_code == 200
    assert dev.json()["status"] == "paid"

    got = (await client.get(f"/v1/payments/{payment_id}", headers=headers)).json()
    assert got["status"] == "paid"
    assert got["approved_at"] is not None

    # deliver the outbox
    sent = await webhooks.process_due_deliveries()
    assert sent >= 1

    # the workspace endpoint received it
    urls = {url for url, _, _ in captured}
    assert "https://sink.example.com/shared" in urls

    url, payload, headers_cap = captured[0]
    assert headers_cap["X-ChmabaPay-Event"] == "payment.completed"
    assert verify_signature(payload, "whsec_testsecret", headers_cap["X-ChmabaPay-Signature"])

    event = json.loads(payload)
    assert event["data"]["payment"]["status"] == "paid"
    assert event["data"]["store"]["id"] == store_a.public_id
    assert event["data"]["store"]["name"] == "Sokha Cafe"
    # the caller's own merchant id travels with every event
    assert event["data"]["merchant"]["external_id"] == "sokha-ext-1"


async def test_store_provisioning_api(client):
    account = await make_account()
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = (
        await client.post(
            "/v1/stores",
            json={"name": "New Store"},
            headers=headers,
        )
    ).json()
    assert created["status"] == "draft"

    linked = (
        await client.put(
            f"/v1/stores/{created['id']}/link",
            json={
                "raw_link": "https://link.payway.com.kh/ownerpayway",
                "merchant_account_id": "ownerpayway",
                "merchant_name": "New Store",
            },
            headers=headers,
        )
    ).json()
    assert linked["status"] == "active"
    assert linked["link"]["merchant_account_id"] == "ownerpayway"

    listing = (await client.get("/v1/stores", headers=headers)).json()
    assert any(s["id"] == created["id"] for s in listing["data"])

    disabled = (
        await client.post(f"/v1/stores/{created['id']}/disable", headers=headers)
    ).json()
    assert disabled["status"] == "disabled"

    # creating a payment for a disabled store fails
    r = await client.post(
        "/v1/payments",
        json={"amount": 1.0, "store": created["id"]},
        headers=headers,
    )
    assert r.status_code == 400

    # enable is the way back. This store still has its link, so it returns to active
    # and can take payments again — before this route existed the disable above was
    # permanent, because both the settings PATCH and the link attach refuse a
    # disabled store.
    enabled = (
        await client.post(f"/v1/stores/{created['id']}/enable", headers=headers)
    ).json()
    assert enabled["status"] == "active"

    r = await client.post(
        "/v1/payments",
        json={"amount": 1.0, "store": created["id"], "hosted_qr": False},
        headers=headers,
    )
    assert r.status_code == 201


async def test_account_key_requires_store_when_multiple_stores(client):
    account = await make_account()
    await make_store(account, name="Sokha Cafe", owner="Sokha")
    await make_store(account, name="Dara Shop", owner="Dara")
    raw_key, _ = await make_key(account)
    r = await client.post(
        "/v1/payments", json={"amount": 1.0}, headers={"Authorization": f"Bearer {raw_key}"}
    )
    assert r.status_code == 400


async def test_payment_list_spans_every_store_on_the_account(client):
    """`GET /v1/payments` with no `store` lists the whole account.

    The portal's payments page asks for "All stores". Creating a payment without a
    store *is* refused on a multi-store account (see the test above), but *listing*
    was refused too, which is a different thing: it left that page rendering "No
    payments yet." for every merchant running more than one store.
    """
    account = await make_account()
    cafe = await make_store(account, name="Sokha Cafe", owner="Sokha")
    shop = await make_store(account, name="Dara Shop", owner="Dara")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    first = (
        await client.post(
            "/v1/payments",
            json={"amount": 1.5, "store": cafe.public_id, "hosted_qr": False},
            headers=headers,
        )
    ).json()
    second = (
        await client.post(
            "/v1/payments",
            json={"amount": 2.5, "store": shop.public_id, "hosted_qr": False},
            headers=headers,
        )
    ).json()

    listing = (await client.get("/v1/payments", headers=headers)).json()
    assert [p["id"] for p in listing["data"]] == [second["id"], first["id"]]
    # Every row names its own store — a cross-store list that cannot say which store
    # a payment belongs to is not readable.
    assert {p["store"] for p in listing["data"]} == {
        cafe.public_id,
        shop.public_id,
    }

    # `paid_at` travels in the list, not only in the single-payment response. The
    # store overview filters exactly this field to build its "Paid today" card.
    assert all(p["paid_at"] is None for p in listing["data"])
    paid = await client.post(f"/_dev/payments/{first['id']}/pay")
    assert paid.status_code == 200
    after = (await client.get("/v1/payments", headers=headers)).json()
    row = next(p for p in after["data"] if p["id"] == first["id"])
    assert row["status"] == "paid"
    assert row["paid_at"] is not None

    # Status filtering still applies across stores, and scoping to one store is
    # unchanged.
    only_paid = (
        await client.get("/v1/payments?status=paid", headers=headers)
    ).json()
    assert [p["id"] for p in only_paid["data"]] == [first["id"]]
    scoped = (
        await client.get(f"/v1/payments?store={shop.public_id}", headers=headers)
    ).json()
    assert [p["id"] for p in scoped["data"]] == [second["id"]]


async def test_expiry_worker(client):
    from datetime import datetime, timedelta

    from chmabapay.db import session_factory
    from chmabapay.services.payments import expire_due_payments

    account = await make_account()
    await make_store(account)
    raw_key, _ = await make_key(account)
    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 3.0, "hosted_qr": False},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    ).json()

    async with session_factory() as session:
        from sqlalchemy import update

        from chmabapay import models

        await session.execute(
            update(models.Payment)
            .where(models.Payment.public_id == created["id"])
            .values(expires_at=datetime.now(UTC) - timedelta(seconds=1))
        )
        await session.commit()
        expired = await expire_due_payments(session)
        assert any(p.public_id == created["id"] for p in expired)

    got = (await client.get(f"/v1/payments/{created['id']}", headers={"Authorization": f"Bearer {raw_key}"})).json()
    assert got["status"] == "expired"


async def test_checkout_pages(client):
    account = await make_account()
    await make_store(account)
    raw_key, _ = await make_key(account)
    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 5.0, "hosted_qr": False},
            headers={"Authorization": f"Bearer {raw_key}"},
        )
    ).json()
    page = await client.get(f"/pay/{created['id']}")
    assert page.status_code == 200
    assert "Sokha Cafe" in page.text
    st = (await client.get(f"/pay/{created['id']}/status")).json()
    assert st["status"] == "pending"
    assert st["amount"] == "5.00"


async def test_checkout_page_renders_branding_and_cannot_be_escaped(client):
    account = await make_account()
    store = await make_store(account, name="Alpha Mart")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}
    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 1.0, "hosted_qr": False},
            headers=headers,
        )
    ).json()

    async with session_factory() as session:
        row = await session.get(models.Account, account.id)
        row.whitelabel_enabled = True
        await session.commit()

    r = await client.patch(
        f"/v1/stores/{store.public_id}",
        json={
            "brand_color": "#0f766e",
            "logo_image_url": "https://cdn.example.com/a.png",
            "whitelabel_css": '.card{border-radius:0}</style><script>alert(1)</script>',
        },
        headers=headers,
    )
    assert r.status_code == 200

    page = await client.get(f"/pay/{created['id']}")
    assert page.status_code == 200
    assert "--red:#0f766e" in page.text
    assert 'class="logo" src="https://cdn.example.com/a.png"' in page.text
    assert ".card{border-radius:0}" in page.text
    # host CSS cannot close the <style> element and inject markup
    assert "</style><script>" not in page.text
    assert "<script>alert(1)</script>" not in page.text


async def test_branding_requires_the_whitelabel_entitlement(client):
    account = await make_account()
    store = await make_store(account, name="Alpha Mart")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}
    created = (
        await client.post(
            "/v1/payments",
            json={"amount": 1.0, "hosted_qr": False},
            headers=headers,
        )
    ).json()

    denied = await client.patch(
        f"/v1/stores/{store.public_id}",
        json={"whitelabel_css": ".card{background:#000}"},
        headers=headers,
    )
    assert denied.status_code == 403
    assert denied.json()["detail"] == "whitelabel_not_enabled"

    # a stored value left over from an earlier entitlement must not render either
    async with session_factory() as session:
        row = await session.get(models.Store, store.id)
        row.brand_color = "#0f766e"
        row.whitelabel_css = ".card{background:#000}"
        await session.commit()

    page = await client.get(f"/pay/{created['id']}")
    assert page.status_code == 200
    assert "--red:#0f766e" not in page.text
    assert "--red:#d92b2b" in page.text
    assert ".card{background:#000}" not in page.text


async def test_payment_resolves_merchant_external_id(client):
    account = await make_account()
    await make_store(account, name="Sokha Cafe", owner="Sokha", external_id="merchant-42")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    r = await client.post(
        "/v1/payments",
        json={"amount": 2.5, "merchant": "merchant-42", "hosted_qr": False},
        headers=headers,
    )
    assert r.status_code == 201
    created = r.json()
    assert created["external_id"] == "merchant-42"

    unknown = await client.post(
        "/v1/payments", json={"amount": 2.5, "merchant": "nope"}, headers=headers
    )
    assert unknown.status_code == 404
    assert unknown.json()["detail"] == "merchant_not_found"


async def test_sub_merchant_and_bank_destination_surfaces_are_gone(client):
    account = await make_account()
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    # the SaaS sub-merchant API and the Bakong/bank destination endpoints no longer exist
    assert (await client.get("/v1/platform/sub-merchants", headers=headers)).status_code == 404
    assert (await client.get("/v1/khqr/bank-codes", headers=headers)).status_code == 404
    assert (
        await client.post(
            "/v1/khqr/from-account",
            json={"bank_code": "ABA", "account_number": "071610243081", "merchant_name": "X", "amount": 1},
            headers=headers,
        )
    ).status_code == 404
    assert (
        await client.post(
            "/v1/transactions/account/check", json={"account_id": "user@bank"}, headers=headers
        )
    ).status_code == 404

    # A store's destination is always an ABA PayWay link, and a value that is not one
    # is refused rather than stored. It used to be accepted and normalised to
    # `link_type=aba_payway` with `verification=verified`, which is how a Bakong
    # account id or a typo became a store that looked ready and failed on the first
    # sale. PayWay-only is now enforced at the write, not just in the column.
    created = (
        await client.post(
            "/v1/stores", json={"name": "PayWay Only"}, headers=headers
        )
    ).json()
    bad = await client.put(
        f"/v1/stores/{created['id']}/link",
        json={"raw_link": "bakong://126071610243081", "merchant_account_id": "126071610243081"},
        headers=headers,
    )
    assert bad.status_code == 400
    assert bad.json()["detail"].startswith("payway_link_invalid:")

    ok = await client.put(
        f"/v1/stores/{created['id']}/link",
        json={
            "raw_link": "https://link.payway.com.kh/ABAPAYpe518710Y",
            "merchant_account_id": "ABAPAYpe518710Y",
        },
        headers=headers,
    )
    assert ok.status_code == 200
    assert ok.json()["link"]["link_type"] == "aba_payway"

    legacy = await client.post(
        f"/v1/stores/{created['id']}/link",
        json={"raw_link": "x", "merchant_account_id": "y"},
        headers=headers,
    )
    assert legacy.status_code == 405  # PUT is the link route; POST is not

    # legacy destination fields are unknown to the schema now: they are ignored,
    # never persisted, and never echoed back
    unwanted = await client.post(
        "/v1/stores",
        json={"name": "Bad Store", "destination_type": "bakong", "destination_details": {"bakong_id": "1"}},
        headers=headers,
    )
    assert unwanted.status_code == 201
    assert "destination_type" not in unwanted.json()
    assert "destination_details" not in unwanted.json()


async def test_every_detection_attempt_is_recorded_not_only_the_first(client):
    """W1's audit trail must grow with each poll.

    `attempt_history` is the only record of *why* a payment sits in the state it
    does, so a payment swept across ABA's whole ~180s window must not read as
    having been checked exactly once. The live rail showed precisely that: three
    real payments, each polled repeatedly, all three holding one attempt — the
    entry was appended to the list already on the row and then that same object
    was assigned back, which SQLAlchemy reads as "unchanged" and drops at flush.
    The first write survived (`None -> [entry]` is a genuine change), which is
    what hid it: the field looked like it worked.

    Driven across *separate sessions* on purpose. Inside one session the list
    identity is the same either way, so the defect is invisible and the test
    would pass against the broken code.
    """
    from sqlalchemy import select

    from chmabapay.workers.w1_payment_detection import PaymentDetectionWorker

    account = await make_account()
    await make_store(account, name="Attempt Store", owner="Attempt")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    # Hosted by default, so this is the path a real PayWay payment takes.
    created = (
        await client.post("/v1/payments", json={"amount": 1.0}, headers=headers)
    ).json()

    async with session_factory() as session:
        payment_id = (
            await session.execute(
                select(models.Payment.id).where(
                    models.Payment.public_id == created["id"]
                )
            )
        ).scalar_one()

    for poll in (1, 2, 3):
        async with session_factory() as session:
            await PaymentDetectionWorker._append_attempt(
                session, payment_id, {"poll": poll}
            )
            await session.commit()

    async with session_factory() as session:
        history = (
            await session.execute(
                select(models.Payment.attempt_history).where(
                    models.Payment.id == payment_id
                )
            )
        ).scalar_one()

    assert [entry["poll"] for entry in history] == [1, 2, 3]


async def test_payment_list_offset_paginates_without_changing_the_first_page(client):
    """`GET /v1/payments` now pages with `offset`, so older rows are reachable.

    A merchant could previously only ever see the newest `limit` payments. The
    first page must stay exactly what it was for a caller that sends no `offset`,
    which is why `offset=0` is not even serialized by the portal.
    """
    account = await make_account()
    await make_store(account, name="Sokha Cafe", owner="Sokha")
    raw_key, _ = await make_key(account)
    headers = {"Authorization": f"Bearer {raw_key}"}

    created = [
        (
            await client.post(
                "/v1/payments",
                json={"amount": 1.0 + index, "hosted_qr": False},
                headers=headers,
            )
        ).json()
        for index in range(3)
    ]
    newest_first = [payment["id"] for payment in reversed(created)]

    first = (await client.get("/v1/payments?limit=2", headers=headers)).json()
    assert [p["id"] for p in first["data"]] == newest_first[:2]

    second = (
        await client.get("/v1/payments?limit=2&offset=2", headers=headers)
    ).json()
    assert [p["id"] for p in second["data"]] == newest_first[2:]

    # The first page is byte-for-byte what it always was: no `offset` and an
    # explicit `offset=0` return the same body, and the shape is unchanged.
    default = (await client.get("/v1/payments", headers=headers)).json()
    explicit_zero = (
        await client.get("/v1/payments?offset=0", headers=headers)
    ).json()
    assert default == explicit_zero
    assert set(default.keys()) == {"data"}
    assert set(default["data"][0].keys()) == {
        "id",
        "status",
        "amount",
        "currency",
        "reference_id",
        "store",
        "created_at",
        "expires_at",
        "approved_at",
        "paid_at",
    }

    # A negative offset is rejected by the parameter declaration (422), not clamped.
    assert (
        await client.get("/v1/payments?offset=-1", headers=headers)
    ).status_code == 422

    # An absurd offset is clamped rather than walked: an empty page, not an error.
    clamped = await client.get("/v1/payments?offset=1000000000", headers=headers)
    assert clamped.status_code == 200
    assert clamped.json()["data"] == []
