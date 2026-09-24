#!/usr/bin/env sh
#
# ChmabaPay database backup, and the restore that proves it.
#
#   deploy/backup.sh                  take a backup, verify it, prune, copy off-host
#   deploy/backup.sh --drill          restore the newest backup into a throwaway
#                                     database and compare it against the live one
#   deploy/backup.sh --restore FILE   restore FILE over the live database
#
# Why this exists: the production database is a Docker volume on the same disk as
# everything else, and the only dumps in this project's history were two taken by
# hand before a destructive migration. A payment platform's transaction history is
# the asset — losing it is not a bad deploy, it is the end of the business. See
# docs/deploy.md section 14.
#
# POSIX sh on purpose. This runs from cron on the VPS, where the shell is `sh` and
# the PATH is nearly empty; nothing here may need bash, a venv, or the repository's
# own toolchain.
#
# Configuration, all optional, all overridable from the environment:
#
#   COMPOSE_FILE     default <repo>/deploy/docker-compose.prod.yml
#   ENV_FILE         default <repo>/deploy/.env
#   BACKUP_DIR       default /var/backups/chmabapay
#   RETENTION_DAYS   default 14
#   BACKUP_REMOTE    an `rclone` destination, e.g. `r2:chmabapay-backups`.
#                    Unset means the copies stay on the same disk as the database,
#                    which is warned about on every run rather than assumed away.
#
# Exit status is the contract with cron and with the alert: non-zero means no
# restore point was produced, and the operator chat is told so.

set -eu

ROOT=$(cd "$(dirname "$0")/.." && pwd)
cd "$ROOT"

COMPOSE_FILE=${COMPOSE_FILE:-"$ROOT/deploy/docker-compose.prod.yml"}
ENV_FILE=${ENV_FILE:-"$ROOT/deploy/.env"}
BACKUP_DIR=${BACKUP_DIR:-/var/backups/chmabapay}
RETENTION_DAYS=${RETENTION_DAYS:-14}
BACKUP_REMOTE=${BACKUP_REMOTE:-}

DRILL_DB=chmabapay_restore_drill
CONFIG_ARCHIVE_PREFIX=chmabapay-config-
DUMP_PREFIX=chmabapay-

# The tables the drill counts on both sides. Deliberately a small, old set: these
# have existed since the first revisions, so a drill that suddenly cannot find one
# is reporting a real schema change rather than tripping over a new table.
DRILL_COUNTS="SELECT (SELECT count(*) FROM accounts) AS accounts, \
(SELECT count(*) FROM stores) AS stores, \
(SELECT count(*) FROM payments) AS payments, \
(SELECT count(*) FROM webhook_endpoints) AS webhook_endpoints, \
(SELECT count(*) FROM alembic_version) AS schema_revisions"

# ---------------------------------------------------------------------------- #
# Output, and the one alert that matters
# ---------------------------------------------------------------------------- #

log() { printf '%s %s\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" "$*"; }

# Read one key out of the env file without sourcing it. `source` would execute the
# file, and a `.env` is not a shell script: one unquoted `#` or space in a value
# and this job dies with a syntax error instead of taking a backup. Last
# definition wins, which is how compose resolves a repeated key.
read_env() {
    [ -f "$ENV_FILE" ] || return 0
    sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*//p" "$ENV_FILE" \
        | tail -n 1 | sed -e 's/^"//' -e 's/"$//'
}

# Tell the operator chat. Best-effort by construction: a failed alert must never
# change the job's outcome, and must never recurse into itself. There is no
# mail-transfer agent on this host and nobody reads root's inbox — an alerting path
# that only logs is the one P0-5 was raised about.
alert() {
    set +e
    token=$(read_env TELEGRAM_BOT_TOKEN)
    chat=$(read_env OPS_TELEGRAM_CHAT_ID)
    if [ -n "$token" ] && [ -n "$chat" ] && command -v curl >/dev/null 2>&1; then
        # The body is read, not just the status code, because Telegram answers HTTP
        # 200 with `"ok":false` when the bot has never been sent /start by that chat —
        # the case section 5 documents. Trusting the status code would report a
        # delivered alert that was never delivered, which is worse than no alert.
        body=$(curl -s -m 10 -X POST \
            "https://api.telegram.org/bot${token}/sendMessage" \
            --data-urlencode "chat_id=${chat}" \
            --data-urlencode "text=$1") || body=""
        case "$body" in
            *'"ok":true'*) : ;;
            *) log "ALERT NOT DELIVERED — Telegram answered: $(printf '%s' "$body" | cut -c1-200)" ;;
        esac
    else
        log "ALERT (not delivered, no Telegram credentials): $1"
    fi
    set -e
}

# Half-written files, removed however the script leaves. A variable rather than a
# second `trap ... EXIT` inside backup(), because there is only one EXIT trap and a
# second would silently replace the alert below — which is a failure path that
# looks like it works.
CLEANUP=""

# Set before an exit that is the script working as designed — a refused restore, a
# bad argument. Those must not page the operator: an alert channel that also fires
# on a typo is one that gets muted, and then the real failure at 03:00 is muted too.
DELIBERATE=""

on_exit() {
    status=$?
    # shellcheck disable=SC2086 # deliberate word splitting: a list of paths
    [ -n "$CLEANUP" ] && rm -f $CLEANUP
    [ "$status" -eq 0 ] && return 0
    if [ -n "$DELIBERATE" ]; then
        log "exit ${status} was a deliberate refusal; no alert sent"
        return 0
    fi
    alert "ChmabaPay backup FAILED on $(hostname) (exit ${status}). No restore point was produced. See docs/deploy.md section 14."
}
trap on_exit EXIT

# ---------------------------------------------------------------------------- #
# Preconditions
# ---------------------------------------------------------------------------- #

compose() { docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"; }

[ -f "$COMPOSE_FILE" ] || { log "FATAL compose file not found: $COMPOSE_FILE"; exit 1; }
[ -f "$ENV_FILE" ] || { log "FATAL env file not found: $ENV_FILE"; exit 1; }
command -v docker >/dev/null 2>&1 || { log "FATAL docker is not on PATH (cron's PATH is not your shell's)"; exit 1; }
docker compose version >/dev/null 2>&1 || { log "FATAL docker compose v2 is not available"; exit 1; }

POSTGRES_USER=$(read_env POSTGRES_USER)
POSTGRES_DB=$(read_env POSTGRES_DB)
POSTGRES_USER=${POSTGRES_USER:-chmaba}
POSTGRES_DB=${POSTGRES_DB:-chmabapay}

DB_CID=$(compose ps -q db 2>/dev/null || true)
[ -n "$DB_CID" ] || { log "FATAL no db container for project in $COMPOSE_FILE"; exit 1; }
[ "$(docker inspect -f '{{.State.Running}}' "$DB_CID" 2>/dev/null || echo false)" = "true" ] \
    || { log "FATAL the db container is not running"; exit 1; }

mkdir -p "$BACKUP_DIR"

# ---------------------------------------------------------------------------- #
# The drill: does the newest dump actually come back?
# ---------------------------------------------------------------------------- #
#
# A dump that has never been restored is a file of unknown value. This restores the
# newest one into a database of its own, on the same cluster, and compares five row
# counts against the live database.
#
# It is NOT part of the daily run, and deliberately so: this host has ONE core and
# 1.9GB of RAM shared with a live POS stack, and a restore is the most expensive
# thing either project does. Run it by hand, watched, after any schema change and
# before any migration — and after any change to this script.

drill() {
    newest=$(ls -1t "$BACKUP_DIR"/${DUMP_PREFIX}*.dump 2>/dev/null | head -n 1 || true)
    [ -n "$newest" ] || { log "FATAL no dump in $BACKUP_DIR to drill"; exit 1; }
    log "drilling $newest ($(wc -c < "$newest") bytes)"

    log "dropping and recreating $DRILL_DB"
    compose exec -T db psql -U "$POSTGRES_USER" -d postgres -q \
        -c "DROP DATABASE IF EXISTS $DRILL_DB" \
        -c "CREATE DATABASE $DRILL_DB"

    log "restoring into the empty database"
    compose exec -T db sh -c "cat > /tmp/drill.dump" < "$newest"
    compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$DRILL_DB" \
        --no-owner --no-acl /tmp/drill.dump

    # Then again, over the database that restore just populated, with the flags the
    # real restore uses. Restoring into an empty database and restoring over a full
    # one are different code paths in pg_restore: `--clean --if-exists` is what makes
    # the second one work, and a drill that never runs it is a drill for the easy case.
    log "restoring again with --clean --if-exists, as the real restore does"
    compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$DRILL_DB" \
        --clean --if-exists --no-owner --no-acl /tmp/drill.dump
    compose exec -T db rm -f /tmp/drill.dump

    log "comparing the restored database against the live one"
    restored=$(compose exec -T db psql -U "$POSTGRES_USER" -d "$DRILL_DB" -At -F' ' -c "$DRILL_COUNTS")
    live=$(compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At -F' ' -c "$DRILL_COUNTS")
    printf '  restored: %s\n  live:     %s\n' "$restored" "$live"

    compose exec -T db psql -U "$POSTGRES_USER" -d postgres -q -c "DROP DATABASE $DRILL_DB"
    log "drill database dropped; the live database was never written to"

    # A mismatch is a warning rather than a failure, and that is the honest reading:
    # the dump is a point in time, so on a system taking payments the live counts
    # legitimately move on between the dump and the drill. A mismatch on
    # `schema_revisions` is the one that always means something — it is written
    # once per migration and never otherwise.
    if [ "$restored" != "$live" ]; then
        log "WARNING counts differ; expected if traffic arrived since the dump. Check schema_revisions."
    else
        log "drill OK — the restore is faithful"
    fi
}

# ---------------------------------------------------------------------------- #
# The restore that overwrites the live database
# ---------------------------------------------------------------------------- #

restore() {
    file=$1
    [ -f "$file" ] || { log "FATAL no such dump: $file"; exit 1; }

    # The only guard that matters. A restore drops and recreates every object it
    # contains, so pointing this at the wrong file — or the right file in the wrong
    # direction — destroys the thing it was meant to protect.
    log "about to restore $file OVER $POSTGRES_DB on $COMPOSE_FILE"
    log "every row written since that dump will be lost. This has no undo."
    if [ "${I_MEAN_IT:-}" != "yes" ]; then
        DELIBERATE=1
        log "refusing. re-run with I_MEAN_IT=yes to proceed."
        exit 1
    fi

    log "stopping api so nothing writes during the restore"
    compose stop api

    log "restoring (this drops and recreates every object in the dump)"
    compose exec -T db sh -c "cat > /tmp/restore.dump" < "$file"
    # --clean --if-exists because a restore into a populated database is the point:
    # without it every CREATE fails against the objects already there, and pg_restore
    # reports that as a wall of errors rather than doing nothing.
    compose exec -T db pg_restore -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
        --clean --if-exists --no-owner --no-acl /tmp/restore.dump
    compose exec -T db rm -f /tmp/restore.dump

    log "starting api again"
    compose start api

    log "restored. verify before trusting it:"
    log "  compose exec -T db psql -U $POSTGRES_USER -d $POSTGRES_DB -c 'SELECT version_num FROM alembic_version'"
}

# ---------------------------------------------------------------------------- #
# The backup
# ---------------------------------------------------------------------------- #

backup() {
    stamp=$(date -u '+%Y-%m-%dT%H%M%SZ')
    final="$BACKUP_DIR/${DUMP_PREFIX}${stamp}.dump"
    incoming="$BACKUP_DIR/.incoming.$$"
    listing="$BACKUP_DIR/.listing.$$"
    CLEANUP="$incoming $listing"

    # An empty log line here is the most useful thing in the output: it is the only
    # evidence that a night with no mail from cron was a night the job ran.
    log "dumping $POSTGRES_DB from $COMPOSE_FILE"

    # Custom format (-Fc), which is compressed and is what pg_restore reads. Written
    # to an incoming name and renamed only after it is verified, so an interrupted
    # run can never leave a plausible-looking zero-byte dump in the rotation.
    #
    # `nice` because this is the heaviest read this stack does and the POS project
    # shares the core. It is not on every image; absent, the job just runs normally.
    if command -v nice >/dev/null 2>&1; then
        nice -n 19 docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" \
            exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$incoming"
    else
        compose exec -T db pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" -Fc > "$incoming"
    fi

    # Verify before publishing. `pg_restore -l` is the check that separates "a file
    # exists" from "an archive can be read" — the first is what a backup that was
    # never tested gives you.
    if [ "$(head -c 5 "$incoming")" != "PGDMP" ]; then
        log "FATAL the dump does not start with the PGDMP magic; not publishing it"
        exit 1
    fi
    compose exec -T db sh -c "cat > /tmp/verify.dump" < "$incoming"
    if ! compose exec -T db pg_restore -l /tmp/verify.dump > "$listing"; then
        compose exec -T db rm -f /tmp/verify.dump
        log "FATAL pg_restore cannot read the dump; not publishing it"
        exit 1
    fi
    compose exec -T db rm -f /tmp/verify.dump

    tables=$(grep -c 'TABLE DATA' "$listing" || true)
    if [ "$tables" -lt 1 ]; then
        log "FATAL the archive lists no table data; not publishing it"
        exit 1
    fi

    mv "$incoming" "$final"
    chmod 600 "$final"

    # The schema revision, kept beside the dump. It is the first question anyone
    # asks during a restore — what does this correspond to — and the answer is not
    # in the archive's name.
    revision=$(compose exec -T db psql -U "$POSTGRES_USER" -d "$POSTGRES_DB" -At \
        -c "SELECT version_num FROM alembic_version" | tr -d '\r' || echo unknown)
    printf 'alembic_revision=%s\ntaken_at=%s\ntables_with_data=%s\n' \
        "$revision" "$stamp" "$tables" > "${final%.dump}.meta"
    chmod 600 "${final%.dump}.meta"

    log "wrote $final ($(wc -c < "$final") bytes, $tables tables with data, schema $revision)"

    # ------------------------------------------------------------------ #
    # The credentials that are not in the dump
    # ------------------------------------------------------------------ #
    # pg_dump does not contain JWT_SECRET_KEY, POSTGRES_PASSWORD or the Bakong
    # credentials — they live only in deploy/.env, and it is the only copy.
    # Losing that file does not lose data, it loses the ability to sign anyone
    # in, and every session and API key in the database becomes unverifiable.
    # deploy/certs/ is a long-lived Cloudflare Origin CA key that can at least be
    # reissued, so it rides along rather than being treated as precious.
    #
    # 600, and never in the same archive as the database: the two have different
    # secrecy requirements and a restore rarely needs both.
    config_archive="$BACKUP_DIR/${CONFIG_ARCHIVE_PREFIX}${stamp}.tar.gz"
    if [ -f "$ENV_FILE" ]; then
        # One tar invocation, not one plus an append: GNU tar refuses to append to a
        # compressed archive ("Cannot update compressed archives"), which is the
        # correct behaviour and would have failed on the VPS too.
        members="${ENV_FILE#"$ROOT"/}"
        if [ -d "$ROOT/deploy/certs" ]; then
            members="$members deploy/certs"
        fi
        tar -czf "$config_archive" -C "$ROOT" $members
        chmod 600 "$config_archive"
        log "wrote $config_archive (env + certs, mode 600 — treat it as a credential)"
    fi

    # ------------------------------------------------------------------ #
    # Off this disk
    # ------------------------------------------------------------------ #
    # A dump beside the database it came from is not a backup against the failure
    # that actually happens: the disk. Set means required, unset means warned
    # about — silently skipping a configured copy would be the worst of the three.
    if [ -n "$BACKUP_REMOTE" ]; then
        command -v rclone >/dev/null 2>&1 \
            || { log "FATAL BACKUP_REMOTE is set but rclone is not installed"; exit 1; }
        log "copying to $BACKUP_REMOTE"
        rclone copyto "$final" "$BACKUP_REMOTE/$(basename "$final")"
        rclone copyto "${final%.dump}.meta" "$BACKUP_REMOTE/$(basename "${final%.dump}.meta")"
        [ -f "$config_archive" ] && rclone copyto "$config_archive" "$BACKUP_REMOTE/$(basename "$config_archive")"
        log "off-host copy done"
    else
        log "WARNING BACKUP_REMOTE is unset: these copies are on the same disk as the database."
        log "WARNING They survive a bad migration and a dropped table. They do not survive the disk."
    fi

    # ------------------------------------------------------------------ #
    # Prune, and only after a good dump is in place
    # ------------------------------------------------------------------ #
    # Local and remote are pruned independently, and a failure to prune is not a
    # failure of the job — the dump is already safe on disk.
    find "$BACKUP_DIR" -maxdepth 1 -type f \
        \( -name "${DUMP_PREFIX}*.dump" -o -name "${DUMP_PREFIX}*.meta" \
           -o -name "${CONFIG_ARCHIVE_PREFIX}*.tar.gz" \) \
        -mtime "+$RETENTION_DAYS" -delete
    if [ -n "$BACKUP_REMOTE" ]; then
        rclone delete --min-age "${RETENTION_DAYS}d" "$BACKUP_REMOTE" >/dev/null 2>&1 || true
    fi

    kept=$(ls -1 "$BACKUP_DIR"/${DUMP_PREFIX}*.dump 2>/dev/null | wc -l || true)
    log "done — $kept dump(s) held locally, retention ${RETENTION_DAYS} days"
}

case "${1:-}" in
    --drill) drill ;;
    --restore)
        shift
        [ $# -eq 1 ] || { DELIBERATE=1; log "usage: $0 --restore FILE"; exit 1; }
        restore "$1"
        ;;
    "") backup ;;
    *) DELIBERATE=1; log "usage: $0 [--drill | --restore FILE]"; exit 1 ;;
esac
