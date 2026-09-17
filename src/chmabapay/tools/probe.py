"""Phase 0 probe — discover what your live Bakong merchant API credentials expose.

This does NOT belong to the product. It is a one-off discovery tool you run against
your Bakong credentials to answer the questions in docs/detection.md §7:

  1. Reachability + auth  (which base URL(s) accept your client_id/secret?)
  2. Which endpoints exist and which auth/body styles they accept
  3. Credential scope     (one merchant account, or on-behalf-of many?)
  4. (optional) Detection latency for a REAL incoming KHQR payment

Security: never commit probe-config.json (it holds a live secret). Keep it out of git.

Run:
  uv run python -m chmabapay.tools.probe --config probe-config.json            # discovery
  uv run python -m chmabapay.tools.probe --config probe-config.json --report findings.json
  uv run python -m chmabapay.tools.probe --config probe-config.json \
       --check-qr "<KHQR payload>" --poll-path /v1/check_transaction_by_khqr    # paid-latency test
"""

from __future__ import annotations

import argparse
import copy
import json
import sys
import time

import httpx

AUTH_BASIC = "basic"


def _trim(text: str, limit: int = 400) -> str:
    text = text.replace("\n", " ").strip()
    return text if len(text) <= limit else text[:limit] + "…"


def _load_config(path: str) -> dict:
    with open(path, encoding="utf-8") as fh:
        cfg = json.load(fh)
    if not cfg.get("client_id") or not cfg.get("client_secret"):
        sys.exit("config must include client_id and client_secret (never commit this file)")
    cfg.setdefault("base_urls", [])
    cfg.setdefault("merchants", [])
    cfg.setdefault("probes", [])
    cfg.setdefault("headers", {})
    if not cfg["base_urls"]:
        sys.exit("config must list at least one base_url (see probe-config.example.json)")
    return cfg


def _auth_for(cfg: dict, probe: dict):
    if probe.get("auth") == AUTH_BASIC:
        return (cfg["client_id"], cfg["client_secret"])
    return None


def _fill(template: str, merchant: dict, extra: dict) -> str:
    vals = {
        "merchant_id": merchant.get("id", ""),
        "on_behalf": merchant.get("on_behalf_of", ""),
        "name": merchant.get("name", ""),
        **extra,
    }
    for key, value in vals.items():
        template = template.replace("{" + key + "}", str(value))
    return template


def run_probe(cfg: dict, merchant: dict, probe: dict, extra: dict | None = None) -> dict:
    method = probe.get("method", "POST").upper()
    path = _fill(probe.get("path", ""), merchant, extra or {})
    # deep-fill body template
    body = copy.deepcopy(probe.get("body", {}))
    if isinstance(body, dict):
        for key in list(body):
            if isinstance(body[key], str):
                body[key] = _fill(body[key], merchant, extra or {})

    result = {
        "name": probe.get("name", path),
        "base": cfg["base_url"],
        "method": method,
        "path": path,
        "body": body,
        "ok": False,
        "http_status": None,
        "elapsed_ms": None,
        "response": None,
        "error": None,
    }
    headers = {"Content-Type": "application/json", **cfg.get("headers", {})}
    try:
        t0 = time.monotonic()
        with httpx.Client(timeout=cfg.get("timeout_seconds", 15), verify=cfg.get("verify", True)) as client:
            resp = client.request(
                method,
                cfg["base"] + path,
                json=body if method == "POST" else None,
                auth=_auth_for(cfg, probe),
                headers=headers,
            )
        result["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        result["http_status"] = resp.status_code
        try:
            result["response"] = resp.json()
        except Exception:
            result["response"] = resp.text[:300]
        result["ok"] = resp.status_code < 300
    except Exception as exc:
        result["error"] = str(exc)[:300]
    return result


def discovery(cfg: dict) -> None:
    print(f"\n== Discovery: {cfg.get('client_id', '')[:6]}… over {len(cfg['base_urls'])} base URL(s), "
          f"{len(cfg.get('merchants', []))} merchant(s) ==")
    overall = {"bases": [], "merchants": [], "probes": []}
    for base in cfg["base_urls"]:
        cfg["base"] = base
        entry = {"base": base, "reachable": False, "error": None}
        try:
            with httpx.Client(timeout=10) as client:
                r = client.get(base, auth=(cfg["client_id"], cfg["client_secret"]))
            entry["reachable"] = r.status_code < 500
            entry["root_status"] = r.status_code
        except Exception as exc:
            entry["error"] = str(exc)[:200]
        print(f"\n[base] {base} reachable={entry['reachable']} "
              f"root_status={entry.get('root_status')} error={entry.get('error')}")
        overall["bases"].append(entry)

    for merchant in cfg.get("merchants", []):
        print(f"\n-- merchant: {merchant.get('name')} id={merchant.get('id')} "
              f"on_behalf_of={merchant.get('on_behalf_of') or '(none)'}")
        merchant_entry = {"merchant": merchant, "results": []}
        for probe in cfg.get("probes", []):
            # run against every base URL the config lists
            for base in cfg["base_urls"]:
                cfg["base"] = base
                res = run_probe(cfg, merchant, probe)
                tag = "OK " if res["ok"] else "---"
                print(f"  {tag} {res['method']:4} {res['base']}{res['path']}"
                      f"  -> {res['http_status']} {res['elapsed_ms']}ms")
                if res["ok"]:
                    print(f"       body: {_trim(json.dumps(res['response']))}")
                elif res["error"]:
                    print(f"       err : {res['error']}")
                merchant_entry["results"].append(res)
        overall["merchants"].append(merchant_entry)
        overall["probes"] = cfg.get("probes", [])

    print("\n== Summary ==")
    for m in overall["merchants"]:
        ok = [r for r in m["results"] if r["ok"]]
        print(f"- merchant {m['merchant'].get('id')}: {len(ok)}/{len(m['results'])} probes OK")
        for r in ok:
            print(f"    {r['method']} {r['path']} @ {r['base']}")
    return overall


def latency_test(cfg: dict, khqr: str, poll_path: str, timeout_seconds: int) -> None:
    body_key_variants = ["qr", "qr_string", "khqr", "data"]
    # pick the first base that answered OK to something during discovery; else first base
    cfg["base"] = cfg["base_urls"][0]
    print("\n== Latency test ==")
    print("Steps (do them NOW):")
    print("  1. Render the QR string below and scan it with your Bakong banking app")
    print("  2. Confirm the payment for a tiny amount")
    print(f"Polling {poll_path} every 2s for up to {timeout_seconds}s…\n")
    print("QR:", khqr)

    started = time.monotonic()
    detected = None
    try:
        with httpx.Client(timeout=10) as client:
            while time.monotonic() - started < timeout_seconds:
                hit = None
                for key in body_key_variants:
                    body = {key: khqr}
                    resp = client.post(
                        cfg["base"] + poll_path,
                        json=body,
                        auth=(cfg["client_id"], cfg["client_secret"]),
                    )
                    if resp.status_code >= 300:
                        continue
                    data = resp.json()
                    if str(data).lower().find("no") < 0 and str(data).lower().find("error") < 0:
                        hit = data
                        break
                if hit is not None:
                    detected = hit
                    break
                time.sleep(2)
    except KeyboardInterrupt:
        print("interrupted")

    elapsed = int(time.monotonic() - started)
    if detected is not None:
        print(f"\nDETECTED after ~{elapsed}s ({cfg['base']}{poll_path}):")
        print(json.dumps(detected, indent=2))
    else:
        print(f"\nNOT detected within {timeout_seconds}s. Check the QR actually paid and that "
              f"{poll_path} + request shape are right (your portal docs).")


def _ask(prompt: str, default: str = "") -> str:
    value = input(f"{prompt} [{default}]: " if default else f"{prompt}: ").strip()
    return value or default


def wizard() -> None:
    """Interactive setup: asks for the values and runs discovery for you."""
    print("\nWelcome to the Bakong test wizard.")
    print("It asks a few questions, saves your details to probe-config.json (kept private,")
    print("never uploaded), then runs the discovery test automatically.\n")
    base = _ask("Bakong URL (leave default for the FREE sandbox)",
                "https://api-sandbox.bakong.com")
    client_id = _ask("Client ID (App ID from your Bakong dashboard)")
    client_secret = _ask("Client secret (App Secret — treat like a password)")
    merchant_id = _ask("Your merchant/Bakong account ID (e.g. something@abaa or a number)")
    merchant_name = _ask("A name for this merchant (e.g. 'Me - sandbox')", "me-sandbox")
    on_behalf = _ask("OPTIONAL: a SECOND merchant ID to test multi-account access (or press Enter to skip)")

    merchants = [{"name": merchant_name, "id": merchant_id, "on_behalf_of": on_behalf}]
    cfg = {
        "base_urls": [base],
        "client_id": client_id,
        "client_secret": client_secret,
        "timeout_seconds": 15,
        "headers": {},
        "merchants": merchants,
        "probes": [
            {"name": "account info (on_behalf_of style)", "method": "POST",
             "path": "/v1/account_information", "auth": "basic",
             "body": {"on_behalf_of": "{on_behalf}"}},
            {"name": "account info (account_id style)", "method": "POST",
             "path": "/v1/account_information", "auth": "basic",
             "body": {"account_id": "{merchant_id}"}},
        ],
    }
    with open("probe-config.json", "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, indent=2)
    print("\nSaved to probe-config.json (this file is git-ignored). Running the test…\n")
    discovery(cfg)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="path to probe config JSON")
    parser.add_argument("--report", help="optional path to write the discovery report JSON")
    parser.add_argument("--check-qr", help="KHQR payload to watch for a real incoming payment")
    parser.add_argument("--poll-path", default="/v1/check_transaction_by_khqr")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument("--wizard", action="store_true",
                        help="guided setup (type values in, no files to edit)")
    args = parser.parse_args()

    if args.wizard:
        wizard()
        return
    if not args.config:
        parser.error("provide --config <file> or use --wizard")
    cfg = _load_config(args.config)
    if args.check_qr:
        latency_test(cfg, args.check_qr, args.poll_path, args.timeout)
        return
    report = discovery(cfg)
    if args.report:
        with open(args.report, "w", encoding="utf-8") as fh:
            json.dump(report, fh, indent=2)
        print(f"\nReport written to {args.report}")


if __name__ == "__main__":
    main()
