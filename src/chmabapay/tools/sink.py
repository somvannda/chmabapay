"""Local webhook sink for development.

Runs on http://127.0.0.1:9000. Point a store's shared webhook here
(WEBHOOK_SINK_URL=http://127.0.0.1:9000/hook) and watch events arrive, including
the X-ChmabaPay-Event and X-ChmabaPay-Signature headers for manual verification.

Run:  uv run python -m chmabapay.tools.sink
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

import uvicorn
from fastapi import FastAPI, Request, Response

app = FastAPI(title="chmabapay webhook sink")

DELIVERIES: list[dict] = []


@app.post("/hook")
async def hook(request: Request):
    body = (await request.body()).decode("utf-8", errors="replace")
    DELIVERIES.append(
        {
            "received_at": datetime.now(UTC).isoformat(),
            "event": request.headers.get("X-ChmabaPay-Event"),
            "signature": request.headers.get("X-ChmabaPay-Signature"),
            "content_type": request.headers.get("Content-Type"),
            "body": json.loads(body) if body else None,
        }
    )
    return Response(status_code=200)


@app.get("/hook")
async def list_hook():
    return {"count": len(DELIVERIES), "deliveries": DELIVERIES}


@app.delete("/hook")
async def clear_hook():
    DELIVERIES.clear()
    return {"count": 0}


if __name__ == "__main__":
    print("webhook sink -> http://127.0.0.1:9000/hook  (GET to list, DELETE to clear)")
    uvicorn.run(app, host="127.0.0.1", port=9000, log_level="warning")
