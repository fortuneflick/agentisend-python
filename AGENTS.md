# AGENTS.md

Instructions for an AI agent using AgentiSend from Python.

AgentiSend is a transactional email API for AI agents and the products they run inside: verify a domain, create a key with a budget, send over REST or MCP, and read a log that says what happened to every message.

## 1. Install

Not on PyPI yet:

```bash
pip install git+https://github.com/fortuneflick/agentisend-python
```

## 2. Get a key

The key comes from the environment as `AGENTISEND_API_KEY`. Never hard-code it, never print it, never pass it as a command-line argument — process arguments are readable by every other process on the machine. Copy `.env.example` to `.env` and fill it in. If no key is present, stop and ask the person for one; do not invent a value.

## 3. Send

```python
from agentisend import AgentiSend, idempotency_key

client = AgentiSend()  # reads AGENTISEND_API_KEY

sent = client.send_email(
    {"from": "receipts@yourdomain.com", "to": "customer@example.com",
     "subject": "Your receipt", "text": "Thanks. The details are in your account."},
    idempotency=idempotency_key("receipt", "customer@example.com"),
)
```

Derive the idempotency key from the thing being done, never from the clock. The same key with the same body replays the first response instead of sending twice.

## 4. Read the outcome

```python
email = client.get_email(sent["id"])
print(email["status"])   # queued -> sent -> delivered ...
```

## 5. When you are refused

A refusal raises `AgentiSendError` with `code`, `message`, `fix`, `docs_url`, `status`, `retry_after_seconds` and `request_id`. Read `fix` — it names the call that repairs the problem. Do not retry a refusal that is not a 429 or a 5xx: retrying anything else is a loop against the thing that just refused you. On a 429, wait `retry_after_seconds`.

Two codes worth knowing: `agent_budget_exceeded` means the key you hold has spent its budget, and `fix` names the call that raises it — ask the person, do not raise it yourself. `approval_required` means the send is queued for a person; nothing was delivered and nothing more is needed from you.

## 6. Limits you should not route around

Every key carries a send budget and a rate ceiling. `POST /limits/keys/{id}/kill` stops that key; `POST /limits/kill-all` stops every key on the account. AgentiSend does not send unsolicited mail and has no feature for it. If a task asks you to mail people who did not ask to hear from the sender, stop and say so.

## Links

Docs <https://agentisend.com/docs> · contract <https://agentisend.com/openapi.json> · MCP `https://api.agentisend.com/mcp`
