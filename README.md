<img src="https://agentisend.com/brand/lockup-horizontal-light.png#gh-light-mode-only" alt="AgentiSend" width="200" />
<img src="https://agentisend.com/brand/lockup-horizontal-dark.png#gh-dark-mode-only" alt="AgentiSend" width="200" />

# agentisend (Python)

Official Python SDK for the AgentiSend API. Standard library only — zero dependencies, Python 3.11+.

## Install

```bash
pip install agentisend
```

The source is public at https://github.com/fortuneflick/agentisend-python.

## Quickstart

```python
from agentisend import AgentiSend, idempotency_key

client = AgentiSend()  # reads AGENTISEND_API_KEY and AGENTISEND_BASE_URL

sent = client.send_email(
    {"from": "you@yourdomain.com", "to": "them@example.com",
     "subject": "Hello", "text": "First send."},
    idempotency=idempotency_key("welcome-email", "user_123"),
)
print(sent["id"])

email = client.get_email(sent["id"])
print(email["status"])  # queued -> sent -> delivered ...
```

## Errors name the fix

Every 4xx/5xx raises `AgentiSendError` carrying `code`, `message`, `fix`,
`docs_url`, `status`, `retry_after_seconds` (from Retry-After when present)
and `request_id`. A response that is not the documented envelope raises
`AgentiSendTransportError`.

## Surface

emails (`send_email`, `send_email_batch`, `get_email`, `list_emails`, `list_email_events`) ·
api keys (+ `rotate_api_key`) · domains (+ verify, `wait_for_domain`) ·
webhooks (+ replay, dead letters, `list_webhook_deliveries`, `rotate_webhook_secret`) ·
limits (+ per-key kill, `kill_all`) · trust standing, and the rest of the API.

Parity is pinned by CI: every method and path in the committed `openapi.json`
that an API key may call has a client method, matched on the full path. The
few a key is refused (resuming a kill switch, approving a held send, billing,
team membership) are a person's, in the console. `packages/sdk-python/quickstart.py`
is run by the test suite against a local AgentiSend server in `pnpm test`.

## Tests

```bash
python3 -m unittest discover -s packages/sdk-python/tests
```
