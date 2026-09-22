"""AgentiSend Python SDK — a thin typed client over the HTTP API.

It adds authentication, JSON, the idempotency header and error shaping — and
nothing else. Everything it can do is something the API can do, which is the
property that lets an agent use either one. Standard library only.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional

from .errors import AgentiSendError, AgentiSendTransportError
from .types import (
    AddressList,
    ApiKey,
    BatchItemResult,
    CreatedApiKey,
    DeadLetter,
    Deleted,
    Domain,
    DomainSetup,
    Email,
    Limit,
    ListEmailsQuery,
    Page,
    ReplayResult,
    SendEmailPayload,
    TrustStanding,
    WebhookEndpoint,
)

DEFAULT_BASE_URL = "https://api.agentisend.com"

__all__ = [
    "AgentiSend",
    "AgentiSendError",
    "AgentiSendTransportError",
    "idempotency_key",
    "DEFAULT_BASE_URL",
]


def idempotency_key(action: str, subject: Any) -> str:
    """Build an idempotency key in the shape that debugs well: ``<what>/<which>``.

    Stable across retries by construction, because it is derived from the thing
    being done rather than from the moment.
    """
    return f"{action}/{subject}"


def _page_query(page: Dict[str, Any]) -> Dict[str, Any]:
    """limit / cursor / after for a list endpoint (M5.2).

    Every list answers in one envelope, ``{data, has_more, next_cursor}``, and
    accepts ``after`` as Resend's name for ``cursor`` — so a migration keeps the
    parameter name it already uses.
    """
    return {k: page[k] for k in ("limit", "cursor", "after") if page.get(k) is not None}


class AgentiSend:
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        timeout_seconds: float = 30.0,
        user_agent: str = "agentisend-python",
    ) -> None:
        key = api_key or os.environ.get("AGENTISEND_API_KEY", "")
        if not key:
            raise ValueError(
                "AgentiSend needs an API key. Pass one to the constructor or set "
                "AGENTISEND_API_KEY. Create one with POST /api-keys."
            )
        self.api_key = key
        self.base_url = (base_url or os.environ.get("AGENTISEND_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.user_agent = user_agent

    def request(
        self,
        method: str,
        path: str,
        query: Optional[Dict[str, Any]] = None,
        body: Optional[Any] = None,
        idempotency: Optional[str] = None,
    ) -> Any:
        qs = ""
        if query:
            filtered = {k: v for k, v in query.items() if v is not None}
            if filtered:
                qs = "?" + urllib.parse.urlencode(filtered)
        url = f"{self.base_url}{path}{qs}"

        headers = {
            "authorization": f"Bearer {self.api_key}",
            "accept": "application/json",
            "user-agent": self.user_agent,
        }
        data = None
        if body is not None:
            headers["content-type"] = "application/json"
            data = json.dumps(body).encode("utf-8")
        if idempotency:
            headers["idempotency-key"] = idempotency

        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                raw = resp.read()
                status = resp.status
                retry_after = resp.headers.get("retry-after")
                request_id = resp.headers.get("x-request-id")
        except urllib.error.HTTPError as err:
            raw = err.read()
            status = err.code
            retry_after = err.headers.get("retry-after") if err.headers else None
            request_id = err.headers.get("x-request-id") if err.headers else None
            self._raise_for_status(
                status, raw, retry_after, request_id, self._rate_limit_from(err.headers)
            )
            raise  # unreachable; _raise_for_status always raises on error status
        except urllib.error.URLError as err:
            raise AgentiSendTransportError(f"Transport failure: {err.reason}", 0) from err

        if status == 204 or len(raw) == 0:
            return None
        try:
            parsed = json.loads(raw.decode("utf-8"))
        except (ValueError, UnicodeDecodeError) as err:
            raise AgentiSendTransportError(
                f"Response was not JSON ({status})", status, raw[:500].decode("utf-8", "replace")
            ) from err
        return parsed

    def request_bytes(self, method: str, path: str, query: Optional[Dict[str, Any]] = None) -> bytes:
        """A response that is not JSON — an export archive, a MIME body."""
        qs = ""
        if query:
            filtered = {k: v for k, v in query.items() if v is not None}
            if filtered:
                qs = "?" + urllib.parse.urlencode(filtered)
        req = urllib.request.Request(
            f"{self.base_url}{path}{qs}",
            headers={
                "authorization": f"Bearer {self.api_key}",
                "accept": "*/*",
                "user-agent": self.user_agent,
            },
            method=method,
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout_seconds) as resp:
                return resp.read()
        except urllib.error.HTTPError as err:
            raw = err.read()
            retry_after = err.headers.get("retry-after") if err.headers else None
            request_id = err.headers.get("x-request-id") if err.headers else None
            self._raise_for_status(
                err.code, raw, retry_after, request_id, self._rate_limit_from(err.headers)
            )
            raise  # unreachable; _raise_for_status always raises on error status
        except urllib.error.URLError as err:
            raise AgentiSendTransportError(f"Transport failure: {err.reason}", 0) from err

    @staticmethod
    def _rate_limit_from(headers: Any) -> Optional[Dict[str, int]]:
        """The ``ratelimit-*`` budget the API reports on every response (M5.1)."""
        if headers is None:
            return None
        pairs = (
            ("limit", "ratelimit-limit"),
            ("remaining", "ratelimit-remaining"),
            ("reset_seconds", "ratelimit-reset"),
        )
        out: Dict[str, int] = {}
        for field, header in pairs:
            raw = headers.get(header)
            if raw is None:
                return None
            try:
                out[field] = int(float(raw))
            except (TypeError, ValueError):
                return None
        return out

    def _raise_for_status(
        self,
        status: int,
        raw: bytes,
        retry_after: Optional[str],
        request_id: Optional[str],
        rate_limit: Optional[Dict[str, int]] = None,
    ) -> None:
        if status < 400:
            return
        text = raw.decode("utf-8", "replace")
        if len(text) == 0:
            raise AgentiSendTransportError(f"Request failed with {status} and an empty body", status)
        try:
            parsed = json.loads(text)
        except ValueError as err:
            raise AgentiSendTransportError(f"Unexpected error shape ({status})", status, text[:500]) from err
        envelope = parsed.get("error") if isinstance(parsed, dict) else None
        if not isinstance(envelope, dict) or "code" not in envelope or "fix" not in envelope:
            raise AgentiSendTransportError(f"Unexpected error shape ({status})", status, text[:500])
        extras_retry: Optional[int] = None
        if retry_after:
            try:
                extras_retry = int(float(retry_after))
            except ValueError:
                extras_retry = None
        raise AgentiSendError(envelope, status, extras_retry, request_id, rate_limit)

    def list_logs(self, **query: Any) -> Dict[str, Any]:
        """Every API request this account made (M5.9).

        Filters: since, until, status, status_class ("2xx"/"4xx"/"5xx"),
        method, route, error_code, api_key_id — plus limit/cursor/after.
        """
        params = _page_query(query)
        for key in (
            "since",
            "until",
            "status",
            "status_class",
            "method",
            "route",
            "error_code",
            "api_key_id",
        ):
            if query.get(key) is not None:
                params[key] = query[key]
        return self.request("GET", "/logs", query=params)

    def get_log(self, log_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/logs/{log_id}")

    def health(self) -> Dict[str, Any]:
        return self.request("GET", "/health")

    def status(self) -> Dict[str, Any]:
        """Public per-component health: what is operational, degraded or down."""
        return self.request("GET", "/status")

    # ---- emails -----------------------------------------------------------

    def send_email(
        self,
        payload: SendEmailPayload,
        idempotency: Optional[str] = None,
    ) -> Dict[str, str]:
        return self.request("POST", "/emails", body=payload, idempotency=idempotency)

    def send_email_batch(
        self,
        payloads: List[SendEmailPayload],
        idempotency: Optional[str] = None,
    ) -> Dict[str, List[BatchItemResult]]:
        """Up to 500 items. Each one succeeds or fails on its own — read data[i].status."""
        return self.request("POST", "/emails/batch", body=payloads, idempotency=idempotency)

    def list_received_emails(self, **query: Any) -> Dict[str, Any]:
        """Mail received at this account's domains (M5.8). Filters: to, from, since, until."""
        params = _page_query(query)
        for key in ("to", "from", "since", "until"):
            if query.get(key) is not None:
                params[key] = query[key]
        return self.request("GET", "/emails/receiving", query=params)

    def get_received_email(self, message_id: str) -> Dict[str, Any]:
        """One received email: headers and bodies as data — nothing is rendered."""
        return self.request("GET", f"/emails/receiving/{message_id}")

    def list_received_threads(self, **query: Any) -> Dict[str, Any]:
        """Received mail grouped by Message-ID / In-Reply-To / References."""
        return self.request("GET", "/emails/receiving/threads", query=_page_query(query))

    def reply_to_received_email(
        self,
        message_id: str,
        payload: Dict[str, Any],
        idempotency: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Reply to one received email. Threads and runs every send check."""
        return self.request(
            "POST",
            f"/emails/receiving/{message_id}/reply",
            body=payload,
            idempotency=idempotency,
        )

    def received_email_raw(self, message_id: str) -> bytes:
        """The stored RFC 5322 source, byte for byte."""
        return self.request_bytes("GET", f"/emails/receiving/{message_id}/raw")

    def list_received_attachments(self, message_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/emails/receiving/{message_id}/attachments")

    def received_attachment_bytes(self, message_id: str, attachment_id: str) -> bytes:
        """Bytes, always as octet-stream — never the sender's declared type."""
        return self.request_bytes(
            "GET", f"/emails/receiving/{message_id}/attachments/{attachment_id}"
        )

    def get_email(self, email_id: str) -> Email:
        return self.request("GET", f"/emails/{email_id}")

    def list_emails(self, query: Optional[ListEmailsQuery] = None) -> Page[Email]:
        return self.request("GET", "/emails", query=query)

    # ---- api keys ---------------------------------------------------------

    def create_api_key(self, name: str, permission: str, domain_scope: Optional[str] = None, scopes: Optional[List[str]] = None, idempotency: Optional[str] = None) -> CreatedApiKey:
        body: Dict[str, Any] = {"name": name, "permission": permission}
        if domain_scope is not None:
            body["domain_scope"] = domain_scope
        if scopes is not None:
            body["scopes"] = scopes
        return self.request("POST", "/api-keys", body=body, idempotency=idempotency)

    def list_api_keys(self, **page: Any) -> Page[ApiKey]:
        return self.request("GET", "/api-keys", query=_page_query(page))

    def update_api_key(
        self, key_id: str, payload: Dict[str, Any], idempotency: Optional[str] = None
    ) -> ApiKey:
        """Rename or re-scope a key. The token is unchanged (M5.5)."""
        return self.request("PATCH", f"/api-keys/{key_id}", body=payload, idempotency=idempotency)

    def delete_api_key(self, key_id: str, idempotency: Optional[str] = None) -> Deleted:
        return self.request("DELETE", f"/api-keys/{key_id}", idempotency=idempotency)

    # ---- domains ----------------------------------------------------------

    def create_domain(
        self,
        name: str,
        region: Optional[str] = None,
        tracking: bool = False,
        tracking_subdomain: Optional[str] = None,
        return_path_subdomain: Optional[str] = None,
        idempotency: Optional[str] = None,
    ) -> Domain:
        """Region is optional and defaults to us (Oregon). eu is Helsinki.

        return_path_subdomain is ``send`` (default) or ``bounce`` when send
        already has an MX pointing elsewhere.
        """
        body: Dict[str, Any] = {"name": name, "tracking": tracking}
        if region is not None:
            body["region"] = region
        if tracking_subdomain is not None:
            body["tracking_subdomain"] = tracking_subdomain
        if return_path_subdomain is not None:
            body["return_path_subdomain"] = return_path_subdomain
        return self.request("POST", "/domains", body=body, idempotency=idempotency)

    def list_domains(self, **page: Any) -> Page[Domain]:
        return self.request("GET", "/domains", query=_page_query(page))

    def get_domain(self, domain_id: str) -> Domain:
        return self.request("GET", f"/domains/{domain_id}")

    def setup_domain(self, domain_id: str) -> DomainSetup:
        """Zone file, copy-paste commands, and provider detection."""
        return self.request("GET", f"/domains/{domain_id}/setup")

    def verify_domain(self, domain_id: str, idempotency: Optional[str] = None) -> Domain:
        return self.request("POST", f"/domains/{domain_id}/verify", idempotency=idempotency)

    def update_domain(
        self,
        domain_id: str,
        click_tracking: Optional[bool] = None,
        open_tracking: Optional[bool] = None,
        tracking_subdomain: Optional[str] = None,
        idempotency: Optional[str] = None,
    ) -> Domain:
        """Click and open tracking, or a new tracking subdomain. The subdomain cannot be removed."""
        body: Dict[str, Any] = {}
        if click_tracking is not None:
            body["click_tracking"] = click_tracking
        if open_tracking is not None:
            body["open_tracking"] = open_tracking
        if tracking_subdomain is not None:
            body["tracking_subdomain"] = tracking_subdomain
        return self.request("PATCH", f"/domains/{domain_id}", body=body, idempotency=idempotency)

    def delete_domain(self, domain_id: str, idempotency: Optional[str] = None) -> Deleted:
        return self.request("DELETE", f"/domains/{domain_id}", idempotency=idempotency)

    # ---- webhooks ---------------------------------------------------------

    def create_webhook(
        self,
        url: str,
        events: Optional[List[str]] = None,
        svix_compat: Optional[bool] = None,
        idempotency: Optional[str] = None,
    ) -> WebhookEndpoint:
        """Register an endpoint. svix_compat adds Svix's headers beside ours (M5.11)."""
        body: Dict[str, Any] = {"url": url}
        if events is not None:
            body["events"] = events
        if svix_compat is not None:
            body["svix_compat"] = svix_compat
        return self.request("POST", "/webhooks", body=body, idempotency=idempotency)

    def list_webhooks(self, **page: Any) -> Page[WebhookEndpoint]:
        return self.request("GET", "/webhooks", query=_page_query(page))

    def get_webhook(self, webhook_id: str) -> WebhookEndpoint:
        return self.request("GET", f"/webhooks/{webhook_id}")

    def update_webhook(self, webhook_id: str, payload: Dict[str, Any], idempotency: Optional[str] = None) -> WebhookEndpoint:
        return self.request("PATCH", f"/webhooks/{webhook_id}", body=payload, idempotency=idempotency)

    def delete_webhook(self, webhook_id: str, idempotency: Optional[str] = None) -> Deleted:
        return self.request("DELETE", f"/webhooks/{webhook_id}", idempotency=idempotency)

    def replay_webhook(
        self,
        webhook_id: str,
        since: Optional[str] = None,
        event_id: Optional[str] = None,
        idempotency: Optional[str] = None,
    ) -> ReplayResult:
        """Re-send past events. Pass since, event_id, or both."""
        query = {"since": since, "event_id": event_id}
        return self.request("POST", f"/webhooks/{webhook_id}/replay", query=query, idempotency=idempotency)

    def list_dead_letters(self, webhook_id: str, limit: Optional[int] = None, cursor: Optional[str] = None, after: Optional[str] = None) -> Page[DeadLetter]:
        return self.request(
            "GET",
            f"/webhooks/{webhook_id}/dead-letters",
            query={"limit": limit, "cursor": cursor, "after": after},
        )

    # ---- limits + trust ---------------------------------------------------

    def list_limits(self, **page: Any) -> Page[Limit]:
        return self.request("GET", "/limits/keys", query=_page_query(page))

    def get_limit(self, api_key_id: str) -> Limit:
        return self.request("GET", f"/limits/keys/{api_key_id}")

    def update_limit(
        self,
        api_key_id: str,
        budget_per_period: Optional[int] = ...,
        period: Optional[str] = None,
        rate_ceiling_per_minute: Optional[int] = ...,
        idempotency: Optional[str] = None,
    ) -> Limit:
        body: Dict[str, Any] = {}
        if budget_per_period is not ...:
            body["budget_per_period"] = budget_per_period
        if period is not None:
            body["period"] = period
        if rate_ceiling_per_minute is not ...:
            body["rate_ceiling_per_minute"] = rate_ceiling_per_minute
        return self.request("PATCH", f"/limits/keys/{api_key_id}", body=body, idempotency=idempotency)

    def kill_key(self, api_key_id: str, reason: Optional[str] = None, idempotency: Optional[str] = None) -> Limit:
        """Stops the key sending, immediately. Takes effect on its next request."""
        body: Dict[str, Any] = {}
        if reason is not None:
            body["reason"] = reason
        return self.request("POST", f"/limits/keys/{api_key_id}/kill", body=body or None, idempotency=idempotency)

    def resume_key(self, api_key_id: str, idempotency: Optional[str] = None) -> Limit:
        return self.request("POST", f"/limits/keys/{api_key_id}/resume", idempotency=idempotency)

    def trust_standing(self) -> TrustStanding:
        return self.request("GET", "/trust/standing")

    def trust_thresholds(self) -> Dict[str, Any]:
        """The published enforcement thresholds and the ladder they drive."""
        return self.request("GET", "/trust/thresholds")

    def export_account(self) -> bytes:
        """Your emails, suppressions and domains as a zip. Bytes, not JSON."""
        return self.request_bytes("GET", "/account/export")

    def close_account(
        self, confirm_email: str, reason: Optional[str] = None, idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        """Close the account: every key revoked, every budget paused, sending stopped.

        `confirm_email` must be an address on the account — the same typed
        confirmation the console asks for. Data stays readable and exportable
        until the retention window ends, so export first if you want a copy.
        """
        body: Dict[str, Any] = {"confirm_email": confirm_email}
        if reason is not None:
            body["reason"] = reason
        return self.request("DELETE", "/account", body=body, idempotency=idempotency)
    # ---- billing --------------------------------------------------------------

    def plan(self) -> Dict[str, Any]:
        """The plan this key's account runs under: tier, entitlements, subscription state.

        Read-only by design — buying happens in the console, never through a key.
        """
        return self.request("GET", "/billing/plan")

    # ---- F8 lifecycle / export ---------------------------------------------

    def cancel_email(self, email_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Cancel a scheduled or queued email. Settled mail is history."""
        return self.request("POST", f"/emails/{email_id}/cancel", idempotency=idempotency)

    def update_email(self, email_id: str, scheduled_at: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Move a scheduled email — the PATCH verb, same act as reschedule (M5.5)."""
        return self.request(
            "PATCH", f"/emails/{email_id}", body={"scheduled_at": scheduled_at}, idempotency=idempotency
        )

    def reschedule_email(self, email_id: str, scheduled_at: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Move an email to a new time — works from scheduled AND canceled."""
        return self.request(
            "POST", f"/emails/{email_id}/reschedule",
            body={"scheduled_at": scheduled_at}, idempotency=idempotency,
        )

    def bulk_cancel_emails(
        self,
        ids: Optional[List[str]] = None,
        before: Optional[str] = None,
        idempotency: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Cancel many emails at once: by ids and/or every pending email before a time."""
        body: Dict[str, Any] = {}
        if ids is not None:
            body["ids"] = ids
        if before is not None:
            body["before"] = before
        return self.request("POST", "/emails/bulk-cancel", body=body, idempotency=idempotency)

    def list_email_attachments(self, email_id: str) -> Dict[str, Any]:
        """What this email carried, and whether the bytes are still there (M5.10)."""
        return self.request("GET", f"/emails/{email_id}/attachments")

    def email_attachment_bytes(self, email_id: str, attachment_id: str) -> bytes:
        """The bytes as they were sent. Served as an inert download."""
        return self.request_bytes("GET", f"/emails/{email_id}/attachments/{attachment_id}")

    def email_mime(self, email_id: str) -> str:
        """The exact RFC 5322 source of this email."""
        return self.request("GET", f"/emails/{email_id}/mime")

    def export_emails(self, query: Optional[Dict[str, Any]] = None) -> str:
        """The message log as CSV."""
        return self.request("GET", "/emails/export.csv", query=query)

    # ---- suppressions ------------------------------------------------------

    def list_suppressions(self, limit: Optional[int] = None, cursor: Optional[str] = None, after: Optional[str] = None) -> Dict[str, Any]:
        return self.request("GET", "/suppressions", query={"limit": limit, "cursor": cursor, "after": after})

    def add_suppression(
        self, email: str, level: Optional[str] = None, idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        """Suppress an address, or a whole domain by sending "@example.com"."""
        body: Dict[str, Any] = {"email": email}
        if level is not None:
            body["level"] = level
        return self.request("POST", "/suppressions", body=body, idempotency=idempotency)

    def batch_add_suppressions(
        self,
        emails: List[str],
        level: Optional[str] = None,
        idempotency: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Up to 500 at a time. Each item succeeds or fails on its own (M5.5)."""
        body: Dict[str, Any] = {"emails": emails}
        if level is not None:
            body["level"] = level
        return self.request("POST", "/suppressions/batch/add", body=body, idempotency=idempotency)

    def batch_remove_suppressions(
        self, emails: List[str], idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        """Lift up to 500 suppressions. An address that was not suppressed is not an error."""
        return self.request(
            "POST", "/suppressions/batch/remove", body={"emails": emails}, idempotency=idempotency
        )

    def remove_suppression(self, suppression_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("DELETE", f"/suppressions/{suppression_id}", idempotency=idempotency)

    # ---- templates (F5 version API) ----------------------------------------

    def create_template(
        self,
        name: str,
        subject: str,
        content: Dict[str, Any],
        variables: Optional[List[Dict[str, Any]]] = None,
        idempotency: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"name": name, "subject": subject, "content": content}
        if variables is not None:
            body["variables"] = variables
        return self.request("POST", "/templates", body=body, idempotency=idempotency)

    def list_templates(self, **page: Any) -> Dict[str, Any]:
        return self.request("GET", "/templates", query=_page_query(page))

    def get_template(self, template_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/templates/{template_id}")

    def update_template(self, template_id: str, payload: Dict[str, Any], idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("PATCH", f"/templates/{template_id}", body=payload, idempotency=idempotency)

    def publish_template(self, template_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("POST", f"/templates/{template_id}/publish", idempotency=idempotency)

    def duplicate_template(
        self, template_id: str, name: Optional[str] = None, idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        """Copy a template into a fresh editable draft — no version history."""
        body: Dict[str, Any] = {}
        if name is not None:
            body["name"] = name
        return self.request(
            "POST", f"/templates/{template_id}/duplicate", body=body, idempotency=idempotency
        )

    def list_template_versions(self, template_id: str, **page: Any) -> Dict[str, Any]:
        return self.request("GET", f"/templates/{template_id}/versions", query=_page_query(page))

    def get_template_version(self, template_id: str, version_number: int) -> Dict[str, Any]:
        return self.request("GET", f"/templates/{template_id}/versions/{version_number}")

    def diff_template_versions(self, template_id: str, from_version: int, to_version: int) -> Dict[str, Any]:
        return self.request(
            "GET", f"/templates/{template_id}/diff", query={"from": from_version, "to": to_version},
        )

    def rollback_template(self, template_id: str, to_version: int, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request(
            "POST", f"/templates/{template_id}/rollback",
            body={"to_version": to_version}, idempotency=idempotency,
        )

    def render_template(
        self,
        template_id: str,
        values: Optional[Dict[str, Any]] = None,
        version_number: Optional[int] = None,
        idempotency: Optional[str] = None,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {"values": values or {}}
        if version_number is not None:
            body["version_number"] = version_number
        return self.request("POST", f"/templates/{template_id}/render", body=body, idempotency=idempotency)

    # ---- trust ---------------------------------------------------------------

    def appeal_trust(self, reason: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """File an appeal against the current standing. Recorded verbatim."""
        return self.request("POST", "/trust/appeal", body={"reason": reason}, idempotency=idempotency)

    # ---- agent actions (B8 approval queue) ---------------------------------

    def list_agent_actions(self, **page: Any) -> Dict[str, Any]:
        return self.request("GET", "/agent-actions", query=_page_query(page))

    def approve_agent_action(self, action_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Execute the held send through the normal accept path."""
        return self.request("POST", f"/agent-actions/{action_id}/approve", idempotency=idempotency)

    def reject_agent_action(
        self, action_id: str, reason: Optional[str] = None, idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {}
        if reason is not None:
            body["reason"] = reason
        return self.request("POST", f"/agent-actions/{action_id}/reject", body=body or None, idempotency=idempotency)

    # ---- deliverability (B3) ------------------------------------------------

    def deliverability(self, domain_id: str) -> Dict[str, Any]:
        """Per-domain reputation: live rates, daily snapshots, external checks."""
        return self.request("GET", f"/deliverability/domains/{domain_id}")

    def snapshot_deliverability(self, domain_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Persist today's rates as a snapshot row (idempotent per day)."""
        return self.request("POST", f"/deliverability/domains/{domain_id}/snapshot", idempotency=idempotency)

    # ---- contacts (F10) ------------------------------------------------------

    def upsert_contact(
        self, email: str, properties: Optional[Dict[str, Any]] = None, idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        """Create or update a contact; properties are typed from their values."""
        body: Dict[str, Any] = {"email": email}
        if properties is not None:
            body["properties"] = properties
        return self.request("POST", "/contacts", body=body, idempotency=idempotency)

    def list_contacts(self, limit: Optional[int] = None, cursor: Optional[str] = None, email: Optional[str] = None, after: Optional[str] = None) -> Dict[str, Any]:
        return self.request(
            "GET", "/contacts", query={"limit": limit, "cursor": cursor, "after": after, "email": email}
        )

    def update_contact(
        self, contact_id: str, payload: Dict[str, Any], idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        """Edit by id. Properties merge; an explicit None removes one (M5.5)."""
        return self.request("PATCH", f"/contacts/{contact_id}", body=payload, idempotency=idempotency)

    def get_contact(self, contact_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/contacts/{contact_id}")

    def delete_contact(self, contact_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("DELETE", f"/contacts/{contact_id}", idempotency=idempotency)

    # ---- topics (M5.4) -------------------------------------------------------

    def create_topic(
        self,
        name: str,
        description: Optional[str] = None,
        default_subscribed: Optional[bool] = None,
        idempotency: Optional[str] = None,
    ) -> Dict[str, Any]:
        """A subscription topic. default_subscribed=False makes it opt-in."""
        body: Dict[str, Any] = {"name": name}
        if description is not None:
            body["description"] = description
        if default_subscribed is not None:
            body["default_subscribed"] = default_subscribed
        return self.request("POST", "/topics", body=body, idempotency=idempotency)

    def list_topics(self, **page: Any) -> Dict[str, Any]:
        return self.request("GET", "/topics", query=_page_query(page))

    def get_topic(self, topic_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/topics/{topic_id}")

    def update_topic(
        self, topic_id: str, payload: Dict[str, Any], idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        return self.request("PATCH", f"/topics/{topic_id}", body=payload, idempotency=idempotency)

    def delete_topic(self, topic_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("DELETE", f"/topics/{topic_id}", idempotency=idempotency)

    def contact_topics(self, contact_id: str) -> Dict[str, Any]:
        """Every topic with this contact's standing on it, and what decided it."""
        return self.request("GET", f"/contacts/{contact_id}/topics")

    def set_contact_topics(
        self,
        contact_id: str,
        topics: List[Dict[str, Any]],
        idempotency: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Record answers: [{"topic_id": ..., "subscribed": True/False}]."""
        return self.request(
            "PATCH", f"/contacts/{contact_id}/topics", body={"topics": topics}, idempotency=idempotency
        )

    # ---- audit log (M4.9) ----------------------------------------------------

    def list_audit_log(
        self,
        actor: Optional[str] = None,
        action: Optional[str] = None,
        since: Optional[str] = None,
        until: Optional[str] = None,
        **page: Any,
    ) -> Dict[str, Any]:
        """This account's own history. `action` takes a family ("api_key") or an exact name."""
        query = _page_query(page)
        if actor is not None:
            query["actor"] = actor
        if action is not None:
            query["action"] = action
        if since is not None:
            query["from"] = since
        if until is not None:
            query["to"] = until
        return self.request("GET", "/audit-log", query=query)

    # ---- team, roles and invites (M4.8) --------------------------------------

    def list_team_members(self, **page: Any) -> Dict[str, Any]:
        """Everyone on the account and the role each one holds."""
        return self.request("GET", "/team/members", query=_page_query(page))

    def team_me(self) -> Dict[str, Any]:
        """Your own role, and the seats the plan allows. Seats are never billed per seat."""
        return self.request("GET", "/team/me")

    def invite_team_member(
        self, email: str, role: str = "viewer", idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        """Invite an address at a role. Membership starts when they accept."""
        return self.request(
            "POST", "/team/invites", body={"email": email, "role": role}, idempotency=idempotency
        )

    def add_team_member(
        self, email: str, role: str = "viewer", idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        """The same call as invite_team_member, under the name people reach for first."""
        return self.request(
            "POST", "/team/members", body={"email": email, "role": role}, idempotency=idempotency
        )

    def set_team_member_role(
        self, member_id: str, role: str, idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        """Change a member's role. The last owner cannot be demoted."""
        return self.request(
            "PATCH", f"/team/members/{member_id}", body={"role": role}, idempotency=idempotency
        )

    def remove_team_member(self, member_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("DELETE", f"/team/members/{member_id}", idempotency=idempotency)

    def list_team_invites(self, **page: Any) -> Dict[str, Any]:
        return self.request("GET", "/team/invites", query=_page_query(page))

    def cancel_team_invite(self, invite_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Cancel a pending invitation; the link in the email stops working at once."""
        return self.request("DELETE", f"/team/invites/{invite_id}", idempotency=idempotency)

    def accept_invite(self, token: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Accept an invitation. Needs a signed-in session as the invited address."""
        return self.request("POST", f"/invite/{token}/accept", body={}, idempotency=idempotency)

    # ---- segments (F6) -------------------------------------------------------

    def create_segment(self, name: str, rules: Optional[List[Dict[str, Any]]] = None, idempotency: Optional[str] = None) -> Dict[str, Any]:
        body: Dict[str, Any] = {"name": name, "rules": rules or []}
        return self.request("POST", "/segments", body=body, idempotency=idempotency)

    def list_segments(self, **page: Any) -> Dict[str, Any]:
        return self.request("GET", "/segments", query=_page_query(page))

    def get_segment(self, segment_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/segments/{segment_id}")

    def segment_members(self, segment_id: str) -> Dict[str, Any]:
        """The segment evaluated live — member ids right now."""
        return self.request("GET", f"/segments/{segment_id}/members")

    def update_segment(self, segment_id: str, payload: Dict[str, Any], idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("PATCH", f"/segments/{segment_id}", body=payload, idempotency=idempotency)

    def delete_segment(self, segment_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("DELETE", f"/segments/{segment_id}", idempotency=idempotency)

    # ---- broadcasts (F14) ----------------------------------------------------

    def create_broadcast(self, payload: Dict[str, Any], idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("POST", "/broadcasts", body=payload, idempotency=idempotency)

    def list_broadcasts(self, **page: Any) -> Dict[str, Any]:
        return self.request("GET", "/broadcasts", query=_page_query(page))

    def cancel_broadcast(self, broadcast_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Cancel a scheduled broadcast: it returns to draft, editable again."""
        return self.request("POST", f"/broadcasts/{broadcast_id}/cancel", idempotency=idempotency)

    def get_broadcast(self, broadcast_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/broadcasts/{broadcast_id}")

    def update_broadcast(self, broadcast_id: str, payload: Dict[str, Any], idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Rename anytime; content edits only while draft."""
        return self.request("PATCH", f"/broadcasts/{broadcast_id}", body=payload, idempotency=idempotency)

    def send_broadcast(self, broadcast_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Send now; content snapshots and becomes immutable."""
        return self.request("POST", f"/broadcasts/{broadcast_id}/send", idempotency=idempotency)

    def archive_broadcast(self, broadcast_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("POST", f"/broadcasts/{broadcast_id}/archive", idempotency=idempotency)

    def broadcast_messages(self, broadcast_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/broadcasts/{broadcast_id}/messages")

    # ---- automations (F7) -----------------------------------------------------

    def create_automation(self, name: str, trigger: Dict[str, Any], steps: List[Dict[str, Any]], idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request(
            "POST", "/automations", body={"name": name, "trigger": trigger, "steps": steps},
            idempotency=idempotency,
        )

    def list_automations(self, **page: Any) -> Dict[str, Any]:
        return self.request("GET", "/automations", query=_page_query(page))

    def get_automation(self, automation_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/automations/{automation_id}")

    def update_automation(self, automation_id: str, payload: Dict[str, Any], idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("PATCH", f"/automations/{automation_id}", body=payload, idempotency=idempotency)

    def enable_automation(self, automation_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("POST", f"/automations/{automation_id}/enable", idempotency=idempotency)

    def disable_automation(self, automation_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("POST", f"/automations/{automation_id}/disable", idempotency=idempotency)

    def list_automation_runs(
        self, automation_id: str, status: Optional[str] = None, **page: Any
    ) -> Dict[str, Any]:
        """Every time this automation fired, newest first (M5.6)."""
        query = _page_query(page)
        if status is not None:
            query["status"] = status
        return self.request("GET", f"/automations/{automation_id}/runs", query=query)

    def get_automation_run(self, automation_id: str, run_id: str) -> Dict[str, Any]:
        """One run with per-step status, output and error."""
        return self.request("GET", f"/automations/{automation_id}/runs/{run_id}")

    def automation_versions(self, automation_id: str, **page: Any) -> Dict[str, Any]:
        return self.request("GET", f"/automations/{automation_id}/versions", query=_page_query(page))

    def list_event_definitions(self, **page: Any) -> Dict[str, Any]:
        """Every custom event this account has declared, with its schema (M5.7)."""
        return self.request("GET", "/events", query=_page_query(page))

    def get_event_definition(self, event_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/events/{event_id}")

    def update_event_definition(
        self, event_id: str, payload: Dict[str, Any], idempotency: Optional[str] = None
    ) -> Dict[str, Any]:
        """Declare an event's fields. A strict event refuses anything it does not declare."""
        return self.request("PATCH", f"/events/{event_id}", body=payload, idempotency=idempotency)

    def delete_event_definition(self, event_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Forget a definition. Ingest keeps working — it just stops being checked."""
        return self.request("DELETE", f"/events/{event_id}", idempotency=idempotency)

    def ingest_event(self, name: str, contact_id: Optional[str] = None, data: Optional[Dict[str, Any]] = None, idempotency: Optional[str] = None) -> Dict[str, Any]:
        """Fire a custom event; matching automations run synchronously."""
        body: Dict[str, Any] = {"name": name}
        if contact_id is not None:
            body["contact_id"] = contact_id
        if data is not None:
            body["data"] = data
        return self.request("POST", "/events", body=body, idempotency=idempotency)

    # ---- dedicated IPs (B6) ---------------------------------------------------

    def ramp_curve(self) -> Dict[str, Any]:
        """The published warmup curve."""
        return self.request("GET", "/dedicated-ips/ramp")

    def list_dedicated_ips(self, **page: Any) -> Dict[str, Any]:
        return self.request("GET", "/dedicated-ips", query=_page_query(page))

    def provision_dedicated_ip(self, ip: str, region: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("POST", "/dedicated-ips", body={"ip": ip, "region": region}, idempotency=idempotency)

    def release_dedicated_ip(self, ip_id: str, idempotency: Optional[str] = None) -> Dict[str, Any]:
        return self.request("DELETE", f"/dedicated-ips/{ip_id}", idempotency=idempotency)

    def route_decision(self, message_id: str) -> Dict[str, Any]:
        return self.request("GET", f"/dedicated-ips/route-decision/{message_id}")

    # ---- metrics (F12) --------------------------------------------------------

    def metrics(self, since: Optional[str] = None, until: Optional[str] = None) -> Dict[str, Any]:
        """KPIs with a daily series, computed on the event spine."""
        return self.request("GET", "/metrics", query={"since": since, "until": until})

    # ---- agent surfaces (preflight / explain / lint / usage / connect) ------

    def preflight_email(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Run every send gate WITHOUT sending; returns the verdict report."""
        return self.request("POST", "/emails/preflight", body=payload)

    def explain_email(self, email_id: str) -> Dict[str, Any]:
        """What happened, the evidence, and the exact calls that fix it."""
        return self.request("GET", f"/emails/{email_id}/explain")

    def lint_email(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        """Deliverability score and findings, without sending."""
        return self.request("POST", "/emails/lint", body=payload)

    def usage(self) -> Dict[str, Any]:
        """The legible bill: per-key budget math and account volume."""
        return self.request("GET", "/usage")

    def billing(self) -> Dict[str, Any]:
        """The plan, the period, usage against every limit it publishes, and
        the overage policy in words. Read-only: nothing here bills."""
        return self.request("GET", "/billing")

    def notifications(self, state: str = "open") -> Dict[str, Any]:
        """Conditions this account should know about. One row per condition,
        counted — never one row per re-fire."""
        return self.request("GET", "/notifications", query={"state": state})

    def read_notification(self, notification_id: str) -> Dict[str, Any]:
        """Mark one notification read. Reading never resolves it."""
        return self.request("POST", f"/notifications/{notification_id}/read", body={})

    def read_all_notifications(self) -> Dict[str, Any]:
        """Mark every open notification read."""
        return self.request("POST", "/notifications/read", body={})

    def notification_preferences(self) -> Dict[str, Any]:
        """Which channels each notification type uses."""
        return self.request("GET", "/notifications/preferences")

    def set_notification_preferences(self, preferences: List[Dict[str, Any]]) -> Dict[str, Any]:
        """Turn a notification type on or off per channel. The gate is the type."""
        return self.request("PATCH", "/notifications/preferences", body={"preferences": preferences})

    def domain_connect(self, domain_id: str) -> Dict[str, Any]:
        """Detect the DNS provider and return the records to add."""
        return self.request("GET", f"/domains/{domain_id}/connect")

        """KPIs with a daily series, computed on the event spine."""
        return self.request("GET", "/metrics", query={"since": since, "until": until})

        """Persist today's rates as a snapshot row (idempotent per day)."""
        return self.request("POST", f"/deliverability/domains/{domain_id}/snapshot", idempotency=idempotency)
