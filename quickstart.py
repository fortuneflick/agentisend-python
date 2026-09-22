"""The AgentiSend Python quickstart — this file is executed by CI.

Run against a local API:

    AGENTISEND_BASE_URL=http://127.0.0.1:5300 \
    AGENTISEND_API_KEY=as_... python3 quickstart.py

Prints one JSON line per step. Exit 0 means every step worked.
"""

from __future__ import annotations

import json
import os
import sys
import time

from agentisend import AgentiSend, idempotency_key


def main() -> int:
    client = AgentiSend()  # reads AGENTISEND_API_KEY / AGENTISEND_BASE_URL

    # 1. Send an email. The idempotency key is derived from the thing being
    #    done, so a retry can never double-send.
    sent = client.send_email(
        {
            "from": "onboarding@example.test",
            "to": "agent@example.test",
            "subject": "Hello from the AgentiSend Python SDK",
            "text": "First send via agentisend-python.",
        },
        idempotency=idempotency_key("welcome-email", "agent-1"),
    )
    print(json.dumps({"step": "send", "id": sent["id"]}))
    email_id = sent["id"]

    # 2. Poll until the message settles (fake/local transport: immediate).
    deadline = time.time() + 15
    while time.time() < deadline:
        email = client.get_email(email_id)
        if email.get("status") in {"sent", "failed", "bounced"}:
            print(json.dumps({"step": "poll", "status": email["status"]}))
            return 0 if email["status"] == "sent" else 1
        time.sleep(0.2)

    print(json.dumps({"step": "poll", "error": "timed out waiting for a terminal status"}))
    return 1


if __name__ == "__main__":
    sys.exit(main())
