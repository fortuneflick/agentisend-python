"""M5.29 / F9 — the Python error object answers "when do I come back?".

A client that meets 429 with no wait hint picks a number, and the number is
usually too small: one rate limit becomes a retry storm against the thing that
was already saturated. The counterpart matters as much — a refusal that waiting
cannot cure must carry NO hint, or a well-behaved client waits forever for
something that will never change.
"""

from __future__ import annotations

import unittest

from agentisend.errors import AgentiSendError


class RetryAfterTest(unittest.TestCase):
    def test_rate_limited_error_carries_the_wait_and_the_window(self) -> None:
        error = AgentiSendError(
            {
                "code": "rate_limit_exceeded",
                "message": "Too many requests.",
                "fix": "Wait for the window to reset, then retry.",
                "docs_url": "https://docs.agentisend.dev/errors#rate_limit_exceeded",
            },
            429,
            retry_after_seconds=17,
            request_id="req_abc",
            rate_limit={"limit": 100, "remaining": 0, "reset_seconds": 17},
        )

        self.assertEqual(error.retry_after_seconds, 17)
        self.assertEqual(error.rate_limit_limit, 100)
        self.assertEqual(error.rate_limit_remaining, 0)
        self.assertEqual(error.rate_limit_reset_seconds, 17)
        self.assertEqual(error.request_id, "req_abc")
        self.assertIn("Wait", error.fix)

    def test_a_refusal_waiting_cannot_cure_carries_no_hint(self) -> None:
        error = AgentiSendError(
            {
                "code": "domain_not_verified",
                "message": "That domain is not verified.",
                "fix": "Publish the DNS records, then POST /domains/:id/verify.",
                "docs_url": "https://docs.agentisend.dev/errors#domain_not_verified",
            },
            422,
        )

        # None, not 0: a zero reads as "retry immediately", which is the
        # opposite of what this refusal means.
        self.assertIsNone(error.retry_after_seconds)
        self.assertIsNone(error.rate_limit_limit)
        self.assertIsNone(error.rate_limit_remaining)

    def test_a_migrating_caller_can_branch_on_the_resend_name(self) -> None:
        error = AgentiSendError(
            {
                "code": "idempotency_payload_mismatch",
                "message": "This key was used with a different payload.",
                "fix": "Use a new key, or send the identical payload.",
                "docs_url": "https://docs.agentisend.dev/errors",
            },
            409,
        )

        self.assertTrue(error.matches("invalid_idempotent_request"))
        self.assertTrue(error.matches("idempotency_payload_mismatch"))
        self.assertFalse(error.matches("rate_limit_exceeded"))

    def test_a_429_that_waiting_cannot_cure_is_not_retryable(self) -> None:
        error = AgentiSendError(
            {
                "code": "daily_quota_exceeded",
                "message": "Account daily quota reached.",
                "fix": "Upgrade the plan or wait for the UTC reset.",
                "docs_url": "https://agentisend.com/docs/errors#daily_quota_exceeded",
                "retryable": False,
            },
            429,
        )
        self.assertFalse(error.retryable)
        self.assertIsNone(error.retry_after_seconds)

    def test_the_message_carries_the_fix_so_a_log_line_is_actionable(self) -> None:
        error = AgentiSendError(
            {
                "code": "suppressed_recipient",
                "message": "Every recipient is suppressed.",
                "fix": "Remove the address via DELETE /suppressions/:id, then retry.",
                "docs_url": "https://docs.agentisend.dev/errors",
            },
            422,
        )
        self.assertIn("DELETE /suppressions/:id", str(error))
        self.assertIn("suppressed_recipient", str(error))


if __name__ == "__main__":
    unittest.main()
