/**
 * The Worker's alert state machine.
 *
 * This is the part that decided to page twice in two days on a single bad
 * minute (2026-11-08 and 2026-11-09). The rules are small but time-based, and a
 * clock bug here is invisible until it wakes someone at 3am, so they are pinned
 * with a controllable clock instead of being left to a manual `wrangler tail`.
 *
 * `run` is private on purpose; everything below drives the public `/run` route
 * with a stubbed KV, a stubbed network, and the real state machine.
 */

import assert from "node:assert/strict";
import { beforeEach, describe, test } from "node:test";

import monitor from "../src/index.js";

const TOKEN = "test-token";

const DNS_DOWN = {
  status: "degraded",
  checks: [
    { name: "telegram", host: "api.telegram.org", ok: false, detail: "DNS timed out after 5.0s" },
    { name: "aba", host: "link.payway.com.kh", ok: false, detail: "DNS timed out after 5.0s" },
    { name: "cloudflare", host: "cloudflare-dns.com", ok: false, detail: "DNS timed out after 5.0s" },
  ],
};

const CONNECT_DOWN = {
  status: "degraded",
  checks: [
    {
      name: "telegram",
      host: "api.telegram.org",
      ok: false,
      detail: "DNS 3ms, connect failed after 5.0s (IPv6 2001:db8::1 timed out)",
    },
    { name: "aba", host: "link.payway.com.kh", ok: true, detail: "DNS 3ms, connect 2ms (IPv4)" },
    { name: "cloudflare", host: "cloudflare-dns.com", ok: false, detail: "DNS 3ms, connect failed" },
  ],
};

const HEALTHY = {
  status: "ok",
  checks: [
    { name: "telegram", host: "api.telegram.org", ok: true, detail: "DNS 3ms, connect 4ms (IPv4)" },
    { name: "aba", host: "link.payway.com.kh", ok: true, detail: "DNS 3ms, connect 2ms (IPv4)" },
    { name: "cloudflare", host: "cloudflare-dns.com", ok: true, detail: "DNS 3ms, connect 3ms (IPv4)" },
  ],
};

let now;
let state;
let writes;
let sent;
let probeReply;
let probeThrows;

Date.now = () => now;

globalThis.fetch = async (url, options) => {
  if (String(url).includes("api.telegram.org")) {
    sent.push(JSON.parse(options.body).text);
    return { ok: true, status: 200, text: async () => "ok" };
  }
  if (probeThrows) {
    throw new TypeError("fetch failed");
  }
  return {
    ok: probeReply === HEALTHY,
    status: probeReply === HEALTHY ? 200 : 503,
    text: async () => JSON.stringify(probeReply),
  };
};

const env = {
  EGRESS_URL: "https://pay.chmaba.com/api/v1/internal/egress",
  EGRESS_TOKEN: TOKEN,
  OPS_CHAT_ID: "1",
  TELEGRAM_BOT_TOKEN: "bot",
  STATE: {
    async get(key, options) {
      const raw = state.get(key);
      if (raw == null) return null;
      return options?.type === "json" ? JSON.parse(raw) : raw;
    },
    async put(key, value) {
      writes += 1;
      state.set(key, value);
    },
  },
};

async function run({ reply = HEALTHY, throws = false }) {
  probeReply = reply;
  probeThrows = throws;
  const response = await monitor.fetch(
    new Request("https://monitor.test/run", {
      headers: { "x-monitor-key": TOKEN },
    }),
    env,
  );
  return response.json();
}

/** One cron tick, one minute after the previous one. */
async function tick(options) {
  const result = await run(options);
  now += 60_000;
  return result;
}

beforeEach(() => {
  now = 1_000_000;
  state = new Map();
  writes = 0;
  sent = [];
  probeReply = HEALTHY;
  probeThrows = false;
});

describe("a single bad minute", () => {
  test("does not page", async () => {
    const result = await tick({ reply: DNS_DOWN });

    assert.equal(result.alerted, false);
    assert.equal(sent.length, 0);
  });

  test("does not page a two-minute blip either", async () => {
    await tick({ reply: DNS_DOWN });
    await tick({ reply: DNS_DOWN });

    assert.equal(sent.length, 0);
  });

  test("resets its window when the host recovers", async () => {
    await tick({ reply: DNS_DOWN });
    await tick({ reply: DNS_DOWN });
    await tick({ reply: HEALTHY });
    await tick({ reply: DNS_DOWN });
    await tick({ reply: DNS_DOWN });

    assert.equal(sent.length, 0, "the two failures after the blip are not consecutive");
  });
});

describe("a sustained outage", () => {
  test("pages once on the third failing minute", async () => {
    await tick({ reply: DNS_DOWN });
    await tick({ reply: DNS_DOWN });
    const third = await tick({ reply: DNS_DOWN });
    await tick({ reply: DNS_DOWN });

    assert.equal(third.alerted, true);
    assert.equal(sent.length, 1);
    assert.match(sent[0], /DNS is failing on the host/);
    assert.match(sent[0], /\/etc\/resolv\.conf/);
  });

  test("reminds at most once an hour, not every minute", async () => {
    for (let minute = 0; minute < 3; minute += 1) {
      await tick({ reply: DNS_DOWN });
    }
    assert.equal(sent.length, 1);

    // Still failing after ten more minutes: silence.
    for (let minute = 0; minute < 10; minute += 1) {
      await tick({ reply: DNS_DOWN });
    }
    assert.equal(sent.length, 1);

    // Cross the hour: exactly one reminder.
    now += 60 * 60_000;
    await tick({ reply: DNS_DOWN });
    await tick({ reply: DNS_DOWN });
    assert.equal(sent.length, 2);
  });

  test("announces recovery after two consecutive passes, with the duration", async () => {
    for (let minute = 0; minute < 3; minute += 1) {
      await tick({ reply: DNS_DOWN });
    }
    assert.equal(sent.length, 1);

    await tick({ reply: HEALTHY });
    assert.equal(sent.length, 1, "one passing minute is not yet a recovery");

    const second = await tick({ reply: HEALTHY });
    assert.equal(second.alerted, true);
    assert.equal(sent.length, 2);
    assert.match(sent[1], /recovered/);
    assert.match(sent[1], /failing for \d+m/);
  });

  test("a relapse during the recovery window is not announced as recovered", async () => {
    for (let minute = 0; minute < 3; minute += 1) {
      await tick({ reply: DNS_DOWN });
    }
    await tick({ reply: HEALTHY });
    await tick({ reply: DNS_DOWN });

    assert.equal(sent.length, 1, "still one alert, no recovery message");
  });
});

describe("what the message says", () => {
  test("a connect failure does not send the reader to the resolver", async () => {
    for (let minute = 0; minute < 3; minute += 1) {
      await tick({ reply: CONNECT_DOWN });
    }

    assert.match(sent[0], /outbound connections are failing/);
    assert.match(sent[0], /not the resolver/);
    assert.doesNotMatch(sent[0], /\/etc\/resolv\.conf/);
  });

  test("an unreachable app is not described as a DNS problem", async () => {
    for (let minute = 0; minute < 3; minute += 1) {
      await tick({ throws: true });
    }

    assert.match(sent[0], /unreachable/);
    assert.match(sent[0], /not a slow resolver/);
  });

  test("names both phases when the resolver and the path are both failing", async () => {
    const mixed = {
      status: "degraded",
      checks: [
        { name: "telegram", host: "api.telegram.org", ok: false, detail: "DNS timed out after 5.0s" },
        {
          name: "aba",
          host: "link.payway.com.kh",
          ok: false,
          detail: "DNS 3ms, connect failed after 5.0s (IPv4 203.0.113.10 timed out)",
        },
        { name: "cloudflare", host: "cloudflare-dns.com", ok: false, detail: "DNS timed out after 5.0s" },
      ],
    };
    for (let minute = 0; minute < 3; minute += 1) {
      await tick({ reply: mixed });
    }

    assert.match(sent[0], /DNS and outbound connections are failing/);
  });
});

describe("KV budget", () => {
  test("writes nothing while healthy", async () => {
    for (let minute = 0; minute < 5; minute += 1) {
      await tick({ reply: HEALTHY });
    }

    assert.equal(writes, 0);
  });

  test("writes nothing on every minute of a sustained outage below the threshold", async () => {
    await tick({ reply: DNS_DOWN });
    const afterFirstFailure = writes;
    await tick({ reply: DNS_DOWN });

    assert.equal(afterFirstFailure, 1, "the failure start is recorded once");
    assert.equal(writes, 1, "the second failing minute changes nothing");
  });
});
