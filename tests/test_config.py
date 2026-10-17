"""Configuration guardrails.

Currently three: the session-signing secret, the queue topology, and which runtime the
configuration was written for. They are checked here rather than through the app because
the checks run in the lifespan, and the test client drives the app over
`httpx.ASGITransport` without a lifespan — so booting the app would never exercise them.
"""

from __future__ import annotations

import pytest

from chmabapay import config
from chmabapay.config import (
    DEV_JWT_SECRET_KEY,
    InsecureSessionSecretError,
    MixedRuntimeError,
    Settings,
    assert_a_queue_will_be_drained,
    assert_runtime_matches_configuration,
    assert_session_secret_is_chosen,
)

CHOSEN = "Qh3f9n2rT7wKz1pYvLb8sXm4cJd6gNa5eU0iRoPqBtV"


@pytest.fixture(autouse=True)
def _no_ambient_runtime_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """A developer's exported runtime variables must not reach these tests.

    Not hypothetical: the machine this was written on had `DATABASE_URL`,
    `REDIS_URL` and `WORKER_TRANSPORT` exported into the shell by an earlier run, and
    an exported variable beats `.env` in pydantic-settings — so every host process
    started from that shell connected to the container's database no matter what the
    file said. That is the mix these guards exist for, and it made the first run of
    these very tests fail for a reason unrelated to the code.
    """
    for name in (
        "CHMABAPAY_RUNTIME",
        "CHMABAPAY_ALLOW_STACK_DB",
        "DATABASE_URL",
        "REDIS_URL",
        "WORKER_TRANSPORT",
        "WORKERS_ENABLED",
    ):
        monkeypatch.delenv(name, raising=False)


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


# --------------------------------------------------------------------------- #
# Runtime identity — container or host
# --------------------------------------------------------------------------- #
# The rules the guard enforces, and the two that matter most are the ones that would
# otherwise *succeed*: a container resolving `localhost` to itself and reading a
# second, empty database, and a host resolving the container's published port and
# reading the stack's data. Neither raises where it happens, so neither is noticed.

CONTAINER_DB = "postgresql+asyncpg://chmaba:chmaba@db:5432/chmabapay"
HOST_DB = "sqlite+aiosqlite:///./chmabapay.db"
CONTAINER_REDIS = "redis://redis:6379/0"


@pytest.fixture
def in_container(monkeypatch: pytest.MonkeyPatch) -> None:
    """This process is in a container. Detection is patched, not simulated.

    Patching the probe rather than `CHMABAPAY_RUNTIME` is the point: the declaration
    must not be able to answer the question it is being checked against.
    """
    monkeypatch.setattr(config, "running_in_container", lambda: True)


@pytest.fixture
def on_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "running_in_container", lambda: False)


def test_the_container_stack_is_allowed(in_container: None) -> None:
    """What docker-compose.yml actually produces: service names and a shared queue."""
    assert (
        assert_runtime_matches_configuration(
            Settings(
                _env_file=None,
                chmabapay_runtime="docker",
                database_url=CONTAINER_DB,
                worker_transport="redis",
                redis_url=CONTAINER_REDIS,
            )
        )
        is None
    )


def test_a_host_run_with_its_own_database_is_allowed(on_host: None) -> None:
    """The other half: a file database has no host to be wrong about."""
    assert (
        assert_runtime_matches_configuration(
            Settings(_env_file=None, chmabapay_runtime="local", database_url=HOST_DB)
        )
        is None
    )


def test_a_host_run_against_a_native_postgres_is_allowed(on_host: None) -> None:
    """CI's arrangement, and a real one: a Postgres the developer runs themselves.

    Only the compose stack's *published* ports are refused, not loopback generally —
    otherwise pointing the tests or a host run at an ordinary local Postgres, which is
    a service this repository never claimed, would be a boot failure.
    """
    assert (
        assert_runtime_matches_configuration(
            Settings(
                _env_file=None,
                chmabapay_runtime="local",
                database_url="postgresql+asyncpg://postgres:postgres@localhost:5432/chmabapay_test",
            )
        )
        is None
    )


def test_a_container_pointed_at_localhost_is_refused(in_container: None) -> None:
    """The silent one: inside a container, `localhost` is the container itself.

    A connection to it does not fail — it lands on whatever runs in that namespace,
    or on nothing, and either way the queries answer from the wrong database.
    """
    with pytest.raises(MixedRuntimeError) as refused:
        assert_runtime_matches_configuration(
            Settings(
                _env_file=None,
                chmabapay_runtime="docker",
                database_url="postgresql+asyncpg://chmaba:chmaba@localhost:55432/chmabapay",
            )
        )

    assert "DATABASE_URL" in str(refused.value)
    assert "localhost" in str(refused.value)


def test_a_host_pointed_at_a_compose_service_name_is_refused(on_host: None) -> None:
    """`db` resolves on the compose network and nowhere else.

    This one fails as a DNS error, which is at least honest — but the message it
    produces says nothing about the runtime, so the guard says it instead.
    """
    with pytest.raises(MixedRuntimeError) as refused:
        assert_runtime_matches_configuration(
            Settings(_env_file=None, chmabapay_runtime="local", database_url=CONTAINER_DB)
        )

    assert "'db'" in str(refused.value)


def test_a_host_pointed_at_the_containers_published_port_is_refused(on_host: None) -> None:
    """The other silent one: 55432 answers on the host, from the stack's volume.

    Every query succeeds. It is the same database the containers use, so it is not
    even wrong in the way a stale file would be — which is exactly why it goes
    unnoticed until two environments disagree about the same row.
    """
    with pytest.raises(MixedRuntimeError) as refused:
        assert_runtime_matches_configuration(
            Settings(
                _env_file=None,
                chmabapay_runtime="local",
                database_url="postgresql+asyncpg://chmaba:chmaba@localhost:55432/chmabapay",
            )
        )

    assert "55432" in str(refused.value)


def test_the_stack_database_can_be_shared_on_purpose(on_host: None) -> None:
    """The opt-in, for the one arrangement the refusal was in the way of.

    A host `uv run uvicorn` against the stack's own Postgres is a decision rather than
    an accident: one dataset, one schema, and the same engine production runs. The
    default stays a refusal — this only stops the guard arguing with a choice that has
    been made explicitly, out loud, in an environment variable.
    """
    assert (
        assert_runtime_matches_configuration(
            Settings(
                _env_file=None,
                chmabapay_runtime="local",
                database_url="postgresql+asyncpg://chmaba:chmaba@localhost:55432/chmabapay",
                chmabapay_allow_stack_db=True,
            )
        )
        is None
    )


def test_the_opt_in_lifts_the_published_port_rule_and_nothing_else(on_host: None) -> None:
    """`db` still does not resolve from the host, opt-in or not.

    The switch is narrower than "allow anything on loopback": a compose service name
    fails here as a DNS error whatever the intent was, so excusing it would trade a
    clear refusal for a confusing one.
    """
    with pytest.raises(MixedRuntimeError) as refused:
        assert_runtime_matches_configuration(
            Settings(
                _env_file=None,
                chmabapay_runtime="local",
                database_url=CONTAINER_DB,
                chmabapay_allow_stack_db=True,
            )
        )

    assert "'db'" in str(refused.value)


def test_the_redis_url_is_only_checked_when_redis_is_the_transport(in_container: None) -> None:
    """Otherwise this guard would refuse every container, out of the box.

    `REDIS_URL`'s default is deliberately a host address, and on the in-process
    transport nothing reads it — so a container running the default topology is
    correct and must not be refused for a variable it never consults. Pairing the
    same URL with `redis` is a different statement, and a wrong one.
    """
    allowed = Settings(
        _env_file=None,
        chmabapay_runtime="docker",
        database_url=CONTAINER_DB,
        worker_transport="inprocess",
        redis_url="redis://localhost:56379/0",
    )
    assert assert_runtime_matches_configuration(allowed) is None

    with pytest.raises(MixedRuntimeError) as refused:
        assert_runtime_matches_configuration(
            allowed.model_copy(update={"worker_transport": "redis"})
        )

    assert "REDIS_URL" in str(refused.value)


def test_a_declaration_that_disagrees_with_the_process_is_refused(on_host: None) -> None:
    """The whole reason the declaration exists.

    `CHMABAPAY_RUNTIME=docker` on a host process is an environment file written for
    the other runtime — the copied-file mistake — and it is caught here before any of
    the address rules get a chance to look reasonable.
    """
    with pytest.raises(MixedRuntimeError) as refused:
        assert_runtime_matches_configuration(
            Settings(_env_file=None, chmabapay_runtime="docker", database_url=CONTAINER_DB)
        )

    assert "CHMABAPAY_RUNTIME" in str(refused.value)


def test_an_unknown_declaration_is_refused(on_host: None) -> None:
    """A typo must not read as "leave it to the probes"."""
    with pytest.raises(MixedRuntimeError):
        assert_runtime_matches_configuration(
            Settings(_env_file=None, chmabapay_runtime="dockerr", database_url=HOST_DB)
        )


def test_the_refusal_names_the_command_that_fixes_it(on_host: None) -> None:
    """A guard that only says "no" gets commented out; this one says which file to use.

    Same reasoning as the session-secret guard above: the remedy has to be in the
    message, or the reader invents one.
    """
    with pytest.raises(MixedRuntimeError) as refused:
        assert_runtime_matches_configuration(
            Settings(_env_file=None, chmabapay_runtime="local", database_url=CONTAINER_DB)
        )

    message = str(refused.value)
    assert "deploy/.env.local" in message
    assert "docker compose --env-file deploy/.env.local" in message
