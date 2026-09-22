# AgentiSend — Python SDK

AgentiSend is a transactional email API for AI agents and the products they run inside: verify a domain, create a key with a budget, send over REST or MCP, and read a log that says what happened to every message.

Standard library only — zero dependencies, Python 3.11 or newer.

## Install

Not on PyPI yet. Install straight from this repository:

```bash
pip install git+https://github.com/fortuneflick/agentisend-python
```

## Send

```python
from agentisend import AgentiSend, idempotency_key

client = AgentiSend()  # reads AGENTISEND_API_KEY

sent = client.send_email(
    {"from": "receipts@yourdomain.com", "to": "customer@example.com",
     "subject": "Your receipt", "text": "Thanks. The details are in your account."},
    idempotency=idempotency_key("receipt", "customer@example.com"),
)
print(sent["id"])

email = client.get_email(sent["id"])
print(email["status"])  # queued -> sent -> delivered ...
```

## Errors name the fix

Every 4xx/5xx raises `AgentiSendError` carrying `code`, `message`, `fix`, `docs_url`, `status`, `retry_after_seconds` (from `Retry-After` when present) and `request_id`. A response that is not the documented envelope raises `AgentiSendTransportError`.

## Surface

emails (`send_email`, `send_email_batch`, `get_email`, `list_emails`) · api keys · domains (+ verify) · webhooks (+ replay, dead letters) · limits (+ kill/resume) · trust standing.

## Tests

```bash
python3 -m unittest discover -s tests
```

## Links

- Docs: <https://agentisend.com/docs>
- API contract: <https://agentisend.com/openapi.json>
- MCP endpoint: `https://api.agentisend.com/mcp` (bearer API key or OAuth 2.1; stdio launcher at [agentisend-mcp-server](https://github.com/fortuneflick/agentisend-mcp-server))
- [AGENTS.md](AGENTS.md) — the short version, for an agent doing this without a person.

Problems: hello@agentisend.com. Licensed MIT.

This repository is generated from the AgentiSend monorepo; open an issue rather than a pull request against generated files.
