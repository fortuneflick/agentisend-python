"""AgentiSend — agent-first email API. Python SDK (standard library only)."""

from .client import DEFAULT_BASE_URL, AgentiSend, idempotency_key
from .errors import RESEND_ERROR_ALIASES, AgentiSendError, AgentiSendTransportError

PACKAGE_NAME = "agentisend"
__version__ = "0.1.0"

__all__ = [
    "AgentiSend",
    "AgentiSendError",
    "AgentiSendTransportError",
    "RESEND_ERROR_ALIASES",
    "idempotency_key",
    "DEFAULT_BASE_URL",
    "PACKAGE_NAME",
    "__version__",
]
