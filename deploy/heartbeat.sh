#!/bin/sh
# Ping an off-box dead-man's switch once a minute.
#
# Why this exists: on 2026-11-04 three dead resolvers made every lookup on this
# host cost ~4-5s. `/health` returned 200 the whole time, the internal payment
# probe never leaves the box, and the only outbound pager (Telegram) used the
# same broken resolver - so nothing was reported and nobody was told. A check
# that has to travel *out* is the one thing that notices this class of failure,
# because its absence is the signal.
#
# Deliberately not a container: it must keep running when the app does not.
#
# Configuration: HEARTBEAT_URL in deploy/.env (a Healthchecks.io / BetterStack
# ping URL). Unset means "no switch configured" and is not a failure - this
# script is inert until someone fills it in.
#
# Exit status is the local evidence: 0 pinged (or not configured), 1 the ping
# could not be sent. The timer fires either way, so one failure does not stop
# the next attempt.

set -u

ENV_FILE=${HEARTBEAT_ENV_FILE:-/opt/chmabapay/deploy/.env}

if [ ! -r "$ENV_FILE" ]; then
    echo "heartbeat: cannot read $ENV_FILE" >&2
    exit 0
fi

# Read one key rather than sourcing the file: compose .env values are not shell
# syntax (spaces and `#` are legal in them), and sourcing would either break or
# execute them.
URL=$(grep -E '^[[:space:]]*HEARTBEAT_URL=' "$ENV_FILE" 2>/dev/null | tail -n 1 | cut -d= -f2- | tr -d '\r"')

case "$URL" in
    http://*|https://*) ;;
    *) exit 0 ;;
esac

if curl -fsS --max-time 10 --retry 2 --retry-delay 2 -o /dev/null "$URL"; then
    exit 0
fi

echo "heartbeat: ping failed - this host could not reach the heartbeat endpoint" >&2
exit 1
