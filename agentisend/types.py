"""Wire types. snake_case end to end — the SDK translates nothing.

`from` is a Python keyword, so the payloads that carry it use the functional
TypedDict form; everything else stays declarative.
"""

from __future__ import annotations

from typing import Any, Dict, List, NotRequired, Optional, TypedDict, Union

AddressList = Union[str, List[str]]

Attachment = TypedDict(
    "Attachment",
    {"filename": str, "content": Optional[str], "path": Optional[str], "content_type": Optional[str]},
)


def _email_payload_fields() -> Dict[str, Any]:
    return {
        "from": str,
        "to": AddressList,
        "subject": str,
        "html": NotRequired[str],
        "text": NotRequired[str],
        "cc": NotRequired[AddressList],
        "bcc": NotRequired[AddressList],
        "reply_to": NotRequired[AddressList],
        "headers": NotRequired[Dict[str, str]],
        "tags": NotRequired[Dict[str, str]],
        "scheduled_at": NotRequired[str],
        "attachments": NotRequired[List[Attachment]],
    }


SendEmailPayload = TypedDict("SendEmailPayload", _email_payload_fields())  # type: ignore[misc]


class Email(TypedDict, total=False):
    id: str
    account_id: str
    api_key_id: Optional[str]
    from_address: str
    from_email: str
    from_domain: str
    to: List[str]
    cc: Optional[List[str]]
    bcc: Optional[List[str]]
    reply_to: Optional[List[str]]
    subject: str
    html: Optional[str]
    text: Optional[str]
    headers: Optional[Dict[str, str]]
    tags: Optional[Dict[str, str]]
    scheduled_at: Optional[str]
    status: str
    last_event: str
    provider_message_id: Optional[str]
    created_at: str
    updated_at: str


class Page(TypedDict, total=False):
    data: List[Any]
    has_more: bool
    next_cursor: Optional[str]


class BatchItemResult(TypedDict, total=False):
    index: int
    status: str  # "accepted" | "rejected"
    id: Optional[str]
    error: Optional[Dict[str, Any]]


class ApiKey(TypedDict, total=False):
    id: str
    name: str
    permission: str
    domain_scope: Optional[str]
    scopes: List[str]
    expires_at: Optional[str]
    token_prefix: str
    created_at: str
    last_used_at: Optional[str]


class CreatedApiKey(TypedDict):
    id: str
    token: str


class Deleted(TypedDict):
    id: str
    deleted: bool


class DomainRecord(TypedDict, total=False):
    record: str
    name: str
    name_relative: str
    host: str
    type: str
    value: str
    value_strings: List[str]
    value_bind: str
    ttl: int
    priority: Optional[int]
    # pending | verified | failed | recommended. "recommended" is DMARC's
    # resting state: advised, never required to verify.
    status: str
    required: bool
    reason: Optional[str]
    observed: Optional[str]
    checked_at: Optional[str]
    fix: str


class Domain(TypedDict, total=False):
    id: str
    account_id: str
    name: str
    zone: str
    region: str
    status: str
    dkim_selector: str
    click_tracking: bool
    open_tracking: bool
    tracking_subdomain: str
    return_path_subdomain: str
    records: List[DomainRecord]
    tracking_issues: List[Dict[str, str]]
    warnings: List[Dict[str, str]]
    created_at: str
    verified_at: Optional[str]


class DomainSetup(TypedDict, total=False):
    domain: str
    zone: str
    detected: bool
    provider: Optional[Dict[str, str]]
    nameservers: List[str]
    records: List[Dict[str, object]]
    zone_file: str
    commands: Dict[str, object]
    deep_link: Optional[str]


class WebhookEndpoint(TypedDict, total=False):
    id: str
    url: str
    events: List[str]
    disabled: bool
    #: True when deliveries also carry svix-id / -timestamp / -signature (M5.11).
    svix_compat: bool
    signing_secret: str
    created_at: str


class DeadLetter(TypedDict, total=False):
    id: str
    payload: Dict[str, Any]
    reason: str
    created_at: str


class ReplayResult(TypedDict):
    replayed: int


class Limit(TypedDict, total=False):
    api_key_id: str
    budget_per_period: int
    period: str
    rate_ceiling_per_minute: int
    consumed_in_period: int
    paused: bool


class TrustStanding(TypedDict, total=False):
    state: str  # "ok" | "warning" | "throttled" | "paused"
    reasons: List[str]
    next_review_at: Optional[str]


ListEmailsQuery = TypedDict(
    "ListEmailsQuery",
    {
        "status": NotRequired[str],
        "from": NotRequired[str],
        "to": NotRequired[str],
        "domain": NotRequired[str],
        "api_key_id": NotRequired[str],
        "since": NotRequired[str],
        "until": NotRequired[str],
        "limit": NotRequired[int],
        "cursor": NotRequired[str],
        # Resend's name for "cursor"; the API accepts either (M5.2).
        "after": NotRequired[str],
    },
)  # type: ignore[misc]
