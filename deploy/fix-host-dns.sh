#!/bin/sh
# Harden the host's resolver, or explain why it needs a different fix.
#
# Why this exists: on 2026-11-04 three of six nameservers stopped answering and
# every lookup paid a ~4s timeout; on 2026-11-09 every lookup timed out for a
# minute. glibc queries nameservers *serially*, so one dead entry stalls each
# lookup before the next is tried, and Docker's embedded DNS forwards the same
# list into every container. The monitors make that visible; this removes it.
#
# It is read-only unless `--apply` is given. With `--apply` it backs the file up,
# writes only resolvers that answered, and restores the backup if a name stops
# resolving afterwards. It refuses to edit a symlink, because that means
# systemd-resolved owns the file - the refusal prints what to configure instead.
#
# Usage:
#   deploy/fix-host-dns.sh                 # inspect, change nothing
#   sudo deploy/fix-host-dns.sh --apply    # write, verify, roll back on failure
#   sudo deploy/fix-host-dns.sh --apply --nameservers "10.0.0.2 10.0.0.3"
#
# Flags:
#   --apply               write the file (default is a dry run)
#   --nameservers "A B"   resolvers to fill in when fewer than two of the
#                         current ones answer (or the whole list with --no-probe)
#   --no-probe            do not test resolvers first (use when dig/nslookup are
#                         unavailable, or when outbound UDP/53 is filtered)
#   --container NAME      after applying, check that NAME resolves via Docker
#   -h, --help            this text
#
# Environment, mainly for tests:
#   RESOLV_CONF   file to manage (default /etc/resolv.conf)
#   VERIFY_HOST   the only name that must resolve after a write; by default the
#                 script tries api.telegram.org, then example.com

set -eu

RESOLV_CONF=${RESOLV_CONF:-/etc/resolv.conf}
FALLBACK=${FALLBACK_NAMESERVERS:-"1.1.1.1 8.8.8.8"}
APPLY=0
PROBE=1
PROPOSED=""
CONTAINER=""

say() { printf '%s\n' "$*"; }
warn() { printf 'warning: %s\n' "$*" >&2; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# Render a whitespace-separated list with exactly one space between words.
join_words() (
    sep=''
    # shellcheck disable=SC2086  # splitting the list is the point
    for word in $1; do
        printf '%s%s' "$sep" "$word"
        sep=' '
    done
)

usage() {
    sed -n '2,/^$/p' "$0" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --apply) APPLY=1 ;;
        --dry-run) APPLY=0 ;;
        --no-probe) PROBE=0 ;;
        --nameservers)
            shift
            [ $# -gt 0 ] || die "--nameservers needs a quoted list, e.g. \"1.1.1.1 8.8.8.8\""
            PROPOSED=$1
            ;;
        --container)
            shift
            [ $# -gt 0 ] || die "--container needs a container name"
            CONTAINER=$1
            ;;
        -h|--help) usage; exit 0 ;;
        *) usage >&2; die "unknown option: $1" ;;
    esac
    shift
done

[ "$(id -u)" -eq 0 ] || [ "$APPLY" -eq 0 ] || die "--apply needs root: run it with sudo"

if [ ! -e "$RESOLV_CONF" ]; then
    die "$RESOLV_CONF does not exist (set RESOLV_CONF to the file you mean)"
fi

if [ -L "$RESOLV_CONF" ]; then
    target=$(readlink "$RESOLV_CONF" 2>/dev/null || echo "?")
    say "refusing to edit $RESOLV_CONF: it is a symlink to $target"
    say ""
    say "That means systemd-resolved owns the resolver. Configure it there instead:"
    say ""
    say "  mkdir -p /etc/systemd/resolved.conf.d"
    say "  cat > /etc/systemd/resolved.conf.d/chmabapay.conf <<'EOF'"
    say "  [Resolve]"
    say "  DNS=1.1.1.1 8.8.8.8"
    say "  FallbackDNS="
    say "  Domains=~."
    say "  EOF"
    say "  systemctl restart systemd-resolved"
    say ""
    say "Then verify with: resolvectl status && getent hosts api.telegram.org"
    exit 1
fi

# --------------------------------------------------------------------------- #
# What is there now
# --------------------------------------------------------------------------- #
# Preserve the operator's order: the first nameserver is the one that answers
# split-horizon/internal names, so sorting would quietly change behaviour.
current=$(awk '/^[[:space:]]*nameserver[[:space:]]+/{if (!seen[$2]++) print $2}' "$RESOLV_CONF")
search_lines=$(awk '/^[[:space:]]*(search|domain)[[:space:]]/{print}' "$RESOLV_CONF" || true)

say "file:     $RESOLV_CONF"
if [ -n "$current" ]; then
    say "current:  $(join_words "$current")"
else
    say "current:  (none)"
fi

# --------------------------------------------------------------------------- #
# Which of them actually answer
# --------------------------------------------------------------------------- #
probe_tool=""
if command -v dig >/dev/null 2>&1; then
    probe_tool=dig
elif command -v nslookup >/dev/null 2>&1; then
    probe_tool=nslookup
fi

if [ "$PROBE" -eq 1 ] && [ -z "$probe_tool" ]; then
    warn "neither dig nor nslookup is installed; skipping the liveness check"
    PROBE=0
fi

answers() {
    ns=$1
    if [ "$probe_tool" = "dig" ]; then
        out=$(dig +short +time=2 +tries=1 "@$ns" example.com A 2>/dev/null || true)
        [ -n "$out" ]
    else
        nslookup example.com "$ns" >/dev/null 2>&1
    fi
}

candidates=""
live=0
if [ "$PROBE" -eq 1 ]; then
    say "probing each resolver (2s each):"
    for ns in $current; do
        if answers "$ns"; then
            say "  ok:   $ns"
            candidates="$candidates $ns"
            live=$((live + 1))
        else
            say "  dead: $ns (dropped)"
        fi
    done
    # Only fill the gap up to two resolvers. A healthy internal DNS must not be
    # replaced by public ones just because the script ran: split-horizon names
    # are answered by the first entry, and adding entries nobody asked for is a
    # change with no failure behind it.
    for ns in ${PROPOSED:-$FALLBACK}; do
        if [ "$live" -ge 2 ]; then
            break
        fi
        case " $candidates " in *" $ns "*) continue ;; esac
        if answers "$ns"; then
            say "  ok:   $ns (fallback)"
            candidates="$candidates $ns"
            live=$((live + 1))
        else
            warn "$ns did not answer; not using it"
        fi
    done
    if [ -z "$(join_words "$candidates")" ]; then
        die "no resolver answered - this looks like a network outage, not a list problem. Change nothing and investigate egress first."
    fi
else
    # No way to test means no way to tell a good list from a dead one. An explicit
    # list wins; otherwise keep what is there and only cap it, rather than replace
    # a working internal resolver with a public one.
    if [ -n "$PROPOSED" ]; then
        candidates=$PROPOSED
        warn "resolvers were not tested; using the list you passed: $(join_words "$candidates")"
    else
        candidates=$current
        warn "resolvers were not tested; keeping the current list and only capping it"
    fi
fi

# Deduplicate and cap at three: glibc does not use more, and Docker only passes
# the first three into a container.
final=""
count=0
for ns in $candidates; do
    case " $final " in *" $ns "*) continue ;; esac
    if [ "$count" -ge 3 ]; then
        warn "ignoring $ns: at most three nameservers are used"
        continue
    fi
    final="$final $ns"
    count=$((count + 1))
done

say "proposed: $(join_words "$final")"
say ""

# --------------------------------------------------------------------------- #
# What would be written
# --------------------------------------------------------------------------- #
build_config() {
    echo "# Managed by deploy/fix-host-dns.sh - see docs/deploy.md"
    [ -z "$search_lines" ] || echo "$search_lines"
    for ns in $final; do
        echo "nameserver $ns"
    done
    echo "options timeout:2 attempts:2 edns0"
}

build_config

if [ "$APPLY" -eq 0 ]; then
    say ""
    say "dry run: nothing written. Re-run with --apply to write it."
    exit 0
fi

# --------------------------------------------------------------------------- #
# Write, verify, roll back
# --------------------------------------------------------------------------- #
tmp=$(mktemp "${RESOLV_CONF}.XXXXXX") || die "cannot create a temporary file next to $RESOLV_CONF"
build_config >"$tmp"
chmod 644 "$tmp"

if cmp -s "$tmp" "$RESOLV_CONF"; then
    rm -f "$tmp"
    say "already up to date; nothing written"
    exit 0
fi

backup="${RESOLV_CONF}.chmabapay.$(date -u '+%Y%m%dT%H%M%SZ')"
cp -p "$RESOLV_CONF" "$backup" || { rm -f "$tmp"; die "cannot back up $RESOLV_CONF"; }

# `mv` is atomic on a normal file, but fails on a bind mount (some VPS images
# mount this file in). Fall back to rewriting in place rather than give up.
if ! mv "$tmp" "$RESOLV_CONF" 2>/dev/null; then
    if ! cat "$tmp" >"$RESOLV_CONF"; then
        rm -f "$tmp"
        die "cannot write $RESOLV_CONF"
    fi
    rm -f "$tmp"
fi
say ""
say "wrote $RESOLV_CONF (backup: $backup)"

verify_one() {
    if command -v timeout >/dev/null 2>&1; then
        timeout 8 getent hosts "$1" >/dev/null 2>&1
    else
        getent hosts "$1" >/dev/null 2>&1
    fi
}

if command -v getent >/dev/null 2>&1; then
    if [ -n "${VERIFY_HOST:-}" ]; then
        ok=0
        verify_one "$VERIFY_HOST" || ok=1
    else
        ok=0
        verify_one api.telegram.org || verify_one example.com || ok=1
    fi
    if [ "$ok" -ne 0 ]; then
        cat "$backup" >"$RESOLV_CONF"
        die "names stopped resolving after the write; restored $backup"
    fi
    say "verified: names resolve again"
else
    warn "getent is not installed; could not verify the result"
fi

if [ -n "$CONTAINER" ]; then
    if command -v docker >/dev/null 2>&1; then
        if docker exec "$CONTAINER" getent hosts example.com >/dev/null 2>&1; then
            say "container $CONTAINER resolves names"
        else
            warn "container $CONTAINER still cannot resolve; compose pins container DNS (x-dns), so check that setting and recreate the container"
        fi
    else
        warn "docker is not installed; skipped the container check"
    fi
fi

if [ -d /etc/cloud/cloud.cfg.d ]; then
    ns_list=""
    for ns in $final; do
        [ -z "$ns_list" ] || ns_list="$ns_list, "
        ns_list="$ns_list'$ns'"
    done
    say ""
    say "cloud-init is present, and a reboot can restore the old list. To make this"
    say "stick, create /etc/cloud/cloud.cfg.d/99-chmabapay-dns.cfg with:"
    say ""
    say "  manage_resolv_conf: true"
    say "  resolv_conf:"
    say "    nameservers: [$ns_list]"
    say "    options: {timeout: 2, attempts: 2}"
fi
