# Stand-in for the private `emergentintegrations` package (Emergent-platform
# only, not on public PyPI - see PROJECT_HANDOVER.md §9) - committed here
# because this app also needs to run in non-Emergent environments (this
# production deployment included), where the real package is simply
# unavailable at any package index. Reimplements just the surface that
# backend/services/subscriptions.py imports
# (StripeCheckout.create_checkout_session/get_checkout_status;
# CheckoutSessionRequest/CheckoutSessionResponse/CheckoutStatusResponse),
# backed by the real `stripe` SDK already in requirements.txt. Not the
# vendor's original code.
#
# `handle_webhook` below is intentionally unused by subscriptions.py's
# `stripe_webhook()` - that function verifies the signature itself via the
# real `stripe.Webhook.construct_event`, since this method never did (it
# only parsed the JSON body with no cryptographic check at all). Left here
# only so removing it isn't a breaking API change to this module; do not
# reintroduce a call to it for webhook handling.
import json
from dataclasses import dataclass
from typing import Any, Dict, Optional

import stripe as _stripe_sdk
from anyio import to_thread


@dataclass
class CheckoutSessionRequest:
    amount: float
    currency: str
    success_url: str
    cancel_url: str
    metadata: Optional[Dict[str, Any]] = None


@dataclass
class CheckoutSessionResponse:
    session_id: str
    url: str


@dataclass
class CheckoutStatusResponse:
    status: str
    payment_status: str
    amount_total: Optional[int] = None
    currency: Optional[str] = None


@dataclass
class WebhookResponse:
    session_id: Optional[str] = None
    payment_status: Optional[str] = None
    event_type: Optional[str] = None


class StripeCheckout:
    def __init__(self, api_key: str, webhook_url: str):
        self.api_key = api_key
        self.webhook_url = webhook_url

    async def create_checkout_session(self, request: CheckoutSessionRequest) -> CheckoutSessionResponse:
        def _create():
            session = _stripe_sdk.checkout.Session.create(
                api_key=self.api_key,
                mode="payment",
                payment_method_types=["card"],
                line_items=[{
                    "price_data": {
                        "currency": request.currency,
                        "unit_amount": int(round(request.amount * 100)),
                        "product_data": {"name": "JAZ Subscription"},
                    },
                    "quantity": 1,
                }],
                success_url=request.success_url,
                cancel_url=request.cancel_url,
                metadata=request.metadata or {},
            )
            return session

        session = await to_thread.run_sync(_create)
        return CheckoutSessionResponse(session_id=session.id, url=session.url)

    async def get_checkout_status(self, session_id: str) -> CheckoutStatusResponse:
        def _retrieve():
            return _stripe_sdk.checkout.Session.retrieve(session_id, api_key=self.api_key)

        session = await to_thread.run_sync(_retrieve)
        return CheckoutStatusResponse(
            status=session.status,
            payment_status=session.payment_status,
            amount_total=session.amount_total,
            currency=session.currency,
        )

    async def handle_webhook(self, body: bytes, signature: str) -> WebhookResponse:
        # No STRIPE_WEBHOOK_SECRET is defined anywhere in this codebase (grep
        # confirms only STRIPE_API_KEY is read), so the original wrapper's
        # signature-verification secret isn't available to reconstruct here.
        # This parses the payload without cryptographic verification -
        # acceptable for local dev where Stripe can't reach localhost anyway.
        payload = json.loads(body)
        data_object = payload.get("data", {}).get("object", {})
        return WebhookResponse(
            session_id=data_object.get("id"),
            payment_status=data_object.get("payment_status"),
            event_type=payload.get("type"),
        )
