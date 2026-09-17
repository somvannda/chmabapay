"""Configuration guardrails.

Currently one: the session-signing secret. It is checked here rather than through the
app because the check runs in the lifespan, and the test client drives the app over
`httpx.ASGITransport` without a lifespan — so booting the app would never exercise it.
"""

from __future__ import annotations

import pytest

from chmabapay.config import (
    DEV_JWT_SECRET_KEY,
    InsecureSessionSecretError,
    Settings,
    assert_a_queue_will_be_drained,
    assert_session_secret_is_chosen,
)

CHOSEN = "Qh3f9n2rT7wKz1pYvLb8sXm4cJd6gNa5eU0iRoPqBtV"


def test_the_shipped_default_is_not_a_usable_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    """With no environment and no `.env`, the app must refuse to start.

    Note what this asserts: the *refusal*, not that the default equals some particular
    string. A default that is itself a secret is the failure being designed out — it
    would sit in the repository and in the image, so it would be public, and a guard
    that compares against one known placeholder would wave it through.

    That is not hypothetical. A real secret briefly stood in as this default, and this
    test is what caught it: the earlier version asserted the default *equalled* the
    placeholder constant, which passed for the wrong reason and then failed the moment
    someone set a working key in the one place it must never live.
    """
    monkeypatch.delenv("JWT_SECRET_KEY", raising=False)
    bare = Settings(_env_file=None)

    with pytest.raises(InsecureSessionSecretError):
        assert_session_secret_is_chosen(bare)


def test_the_published_placeholder_is_refused() -> None:
    """An environment file written before the default changed still carries this.

    A placeholder everyone knows is exactly as forgeable as a blank one, so it stays
    refused even though nothing ships it any more.
    """
    with pytest.raises(InsecureSessionSecretError):
        assert_session_secret_is_chosen(Settings(jwt_secret_key=DEV_JWT_SECRET_KEY))


@pytest.mark.parametrize("blank", ["", "   ", "\t"])
def test_a_blank_secret_is_refused(blank: str) -> None:
    """Empty is not a secret, and it is what a copied `.env.example` holds.

    An HS256 key of `""` is not "no signing" — it is signing with a value every
    attacker already knows, which is worse than a placeholder because it looks
    deliberate.
    """
    with pytest.raises(InsecureSessionSecretError):
        assert_session_secret_is_chosen(Settings(jwt_secret_key=blank))


def test_a_chosen_secret_is_accepted() -> None:
    assert_session_secret_is_chosen(Settings(jwt_secret_key=CHOSEN)) is None


def test_the_refusal_tells_the_operator_what_to_do() -> None:
    """A refusal that does not say how to fix it gets commented out instead.

    So the message must name the variable and carry a command that produces a value —
    which is the difference between a guard that holds and a guard someone deletes at
    3am to get a deploy out.
    """
    with pytest.raises(InsecureSessionSecretError) as refused:
        assert_session_secret_is_chosen(Settings(jwt_secret_key=DEV_JWT_SECRET_KEY))

    message = str(refused.value)
    assert "JWT_SECRET_KEY" in message
    assert "secrets.token_urlsafe" in message
    assert ".env.example" in message


def test_workers_off_with_an_in_process_queue_is_refused() -> None:
    """The forgotten-variable case, and it is one variable deep.

    `WORKERS_ENABLED=false` says the drains are elsewhere. With the default
    in-process transport there is no elsewhere: this process holds a queue nobody can
    see, and every job it enqueues is dropped in a swallowed exception. The symptom
    would be payments that are taken and never detected, which is worth refusing to
    boot over.
    """
    with pytest.raises(RuntimeError):
        assert_a_queue_will_be_drained(
            Settings(workers_enabled=False, worker_transport="inprocess")
        )


def test_workers_off_with_a_shared_queue_is_allowed() -> None:
    """The split topology — the arrangement the switch exists for."""
    assert (
        assert_a_queue_will_be_drained(
            Settings(workers_enabled=False, worker_transport="redis")
        )
        is None
    )


def test_workers_on_is_allowed_without_a_shared_queue() -> None:
    """The default topology must keep working with no external queue at all."""
    assert (
        assert_a_queue_will_be_drained(
            Settings(workers_enabled=True, worker_transport="inprocess")
        )
        is None
    )


def test_the_queue_refusal_names_both_variables() -> None:
    """Knowing which knob to turn is the difference between a fix and a workaround."""
    with pytest.raises(RuntimeError) as refused:
        assert_a_queue_will_be_drained(
            Settings(workers_enabled=False, worker_transport="inprocess")
        )

    message = str(refused.value)
    assert "WORKER_TRANSPORT" in message
    assert "WORKERS_ENABLED" in message
