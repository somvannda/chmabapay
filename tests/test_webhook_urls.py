"""Webhook endpoint URLs point the platform's own POST somewhere.

So the create and update routes have to refuse the shapes that are never a receiver
and the ones that would aim the request at our network. The incident that prompted
this was not an SSRF - it was a URL that had never accepted a POST - but a merchant
could equally have pasted `http://localhost:8000` and made the sender talk to itself.
"""

from __future__ import annotations

import httpx
import pytest
import pytest_asyncio
from conftest import make_account, session_client

from chmabapay.main import app

BASE_URL = "http://localhost"


@pytest_asyncio.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url=BASE_URL) as c:
        yield c


@pytest.mark.parametrize(
    "url",
    [
        "file:///etc/passwd",
        "ftp://hooks.example.com/notify",
        "https://localhost:8000/hook",
        "http://127.0.0.1/hook",
        "http://10.0.0.5/hook",
        "http://192.168.1.10/hook",
        "http://169.254.169.254/latest/meta-data",
        "http://[::1]/hook",
        "https://user:password@hooks.example.com/notify",
    ],
)
async def test_a_url_that_cannot_be_a_receiver_is_refused(client, url):
    account = await make_account()

    async with session_client(account) as portal:
        res = await portal.post("/api/v1/webhooks", json={"url": url})

    assert res.status_code == 422, res.text


async def test_a_public_https_url_is_accepted(client):
    account = await make_account()

    async with session_client(account) as portal:
        res = await portal.post(
            "/api/v1/webhooks", json={"url": "https://hooks.example.com/chmabapay"}
        )

    assert res.status_code == 201, res.text
    assert res.json()["url"] == "https://hooks.example.com/chmabapay"


async def test_an_edit_cannot_move_an_endpoint_to_a_private_address(client):
    account = await make_account()

    async with session_client(account) as portal:
        created = await portal.post(
            "/api/v1/webhooks", json={"url": "https://hooks.example.com/chmabapay"}
        )
        assert created.status_code == 201, created.text
        endpoint_id = created.json()["id"]

        res = await portal.patch(
            f"/api/v1/webhooks/{endpoint_id}",
            json={"url": "http://127.0.0.1:8000/internal"},
        )

    assert res.status_code == 422, res.text
