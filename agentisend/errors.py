"""AgentiSend errors — every 4xx/5xx arrives with a `fix` (PRD §2)."""

from __future__ import annotations

from typing import Any, Dict, Optional


#: Resend's error names, resolved onto our catalogue codes (M5.3). Most already
#: match; the two idempotency outcomes we named for what happened rather than
#: for the request do not. The API's ``migration_resend_error_names_resolve``
#: test asserts this table equals the catalogue's, so the two cannot drift.
RESEND_ERROR_ALIASES: Dict[str, str] = {
    "missing_api_key": "missing_api_key",
    "restricted_api_key": "restricted_api_key",
    "invalid_api_key": "invalid_api_key",
    "invalid_attachment": "invalid_attachment",
    "invalid_from_address": "invalid_from_address",
    "daily_quota_exceeded": "daily_quota_exceeded",
    "method_not_allowed": "method_not_allowed",
    "concurrent_idempotent_requests": "idempotency_in_flight",
    "invalid_idempotent_request": "idempotency_payload_mismatch",
    "validation_error": "validation_error",
    "not_found": "not_found",
    "rate_limit_exceeded": "rate_limit_exceeded",
    "internal_server_error": "internal_server_error",
}


class AgentiSendError(Exception):
    """A structured API error.

    Attributes mirror the wire envelope ``{error: {code, message, fix, docs_url}}``.
    ``retry_after_seconds`` is populated from the Retry-After header whenever the
    API sends one; ``request_id`` echoes the server's x-request-id for support.

    ``rate_limit_limit`` / ``rate_limit_remaining`` / ``rate_limit_reset_seconds``
    carry the ``ratelimit-*`` headers the API sends on every response (M5.1), so
    a caller learns how much room is left, not only that there was none.
    """

    def __init__(
        self,
        payload: Dict[str, Any],
        status: int,
        retry_after_seconds: Optional[int] = None,
        request_id: Optional[str] = None,
        rate_limit: Optional[Dict[str, int]] = None,
    ) -> None:
        self.code: str = payload.get("code", "unknown")
        self.message: str = payload.get("message", "")
        self.fix: str = payload.get("fix", "")
        self.docs_url: str = payload.get("docs_url", "")
        self.status: int = status
        if "retryable" in payload:
            self.retryable: bool = bool(payload["retryable"])
        else:
            self.retryable = status == 429 or status >= 500
        body_wait = payload.get("retry_after_seconds")
        if isinstance(body_wait, (int, float)) and body_wait > 0:
            self.retry_after_seconds: Optional[int] = int(body_wait)
        else:
            self.retry_after_seconds = retry_after_seconds
        self.request_id = request_id
        limits = rate_limit or {}
        self.rate_limit_limit: Optional[int] = limits.get("limit")
        self.rate_limit_remaining: Optional[int] = limits.get("remaining")
        self.rate_limit_reset_seconds: Optional[int] = limits.get("reset_seconds")
        super().__init__(f"[{self.code}] {self.message} — {self.fix}")

    def matches(self, name: str) -> bool:
        """Does this error answer to ``name``?

        Accepts our code or Resend's name for the same thing, so a migrating
        ``if err.name == "invalid_idempotent_request"`` keeps working.
        """
        return name == self.code or RESEND_ERROR_ALIASES.get(name) == self.code

    def __str__(self) -> str:
        return f"AgentiSendError [{self.code}]: {self.message} — {self.fix}"


class AgentiSendTransportError(Exception):
    """The response was not the documented envelope at all."""

    def __init__(self, message: str, status: int, body_snippet: str = "") -> None:
        self.status = status
        self.body_snippet = body_snippet
        super().__init__(message)
