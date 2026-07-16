import os
from datetime import datetime, timedelta, timezone

from emergentintegrations.payments.stripe.checkout import (
    CheckoutSessionRequest,
    CheckoutSessionResponse,
    CheckoutStatusResponse,
    StripeCheckout,
)
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.companies as companies_repo
import repositories.payment_transactions as payment_transactions_repo
import repositories.subscription_plans as subscription_plans_repo
import services.notifications as notifications_service
from services.admin import parse_uuid, plan_to_response


def _stripe_client(webhook_url: str) -> StripeCheckout:
    stripe_key = os.environ.get("STRIPE_API_KEY")
    if not stripe_key:
        raise HTTPException(status_code=500, detail="Stripe not configured")
    return StripeCheckout(api_key=stripe_key, webhook_url=webhook_url)


async def create_checkout(db: AsyncSession, current_user: dict, plan_id: str, origin_url: str, host_url: str) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Only company owners can subscribe")

    parsed_plan_id = parse_uuid(plan_id)
    plan = await subscription_plans_repo.get_by_id(db, parsed_plan_id) if parsed_plan_id else None
    if not plan:
        raise HTTPException(status_code=404, detail="Plan not found")

    amount = float(plan.price)
    currency = "usd"
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = _stripe_client(webhook_url)

    success_url = f"{origin_url}/company-owner/subscription?session_id={{CHECKOUT_SESSION_ID}}"
    cancel_url = f"{origin_url}/company-owner/subscription"
    metadata = {"user_id": current_user["id"], "company_id": current_user["company_id"], "plan_id": plan_id}

    checkout_req = CheckoutSessionRequest(
        amount=amount, currency=currency, success_url=success_url, cancel_url=cancel_url, metadata=metadata,
    )
    session: CheckoutSessionResponse = await stripe_checkout.create_checkout_session(checkout_req)

    await payment_transactions_repo.create(
        db, session_id=session.session_id, user_id=parse_uuid(current_user["id"]),
        company_id=parse_uuid(current_user["company_id"]), plan_id=parsed_plan_id, amount=amount, currency=currency,
        transaction_metadata=metadata, payment_status="initiated", status="pending",
    )
    return {"url": session.url, "session_id": session.session_id}


async def _activate_from_transaction(db: AsyncSession, tx) -> None:
    plan = await subscription_plans_repo.get_by_id(db, tx.plan_id)
    if plan:
        end_date = datetime.now(timezone.utc) + timedelta(days=30 * plan.duration_months)
        await companies_repo.activate_subscription(db, tx.company_id, tx.plan_id, end_date)

        await notifications_service.publish(
            db,
            user_id=tx.user_id,
            company_id=tx.company_id,
            category=notifications_service.CATEGORY_PAYMENTS,
            type="payment_received",
            title="تم استلام الدفعة",
            message=f"تم تفعيل اشتراك خطة {plan.name} بنجاح",
            entity_type="payment_transaction",
            entity_id=tx.id,
            action_url="/company-owner/subscription",
        )
        await notifications_service.publish_to_role(
            db,
            role="super_admin",
            category=notifications_service.CATEGORY_PAYMENTS,
            type="payment_received",
            title="دفعة جديدة",
            message=f"تم استلام دفعة بقيمة {tx.amount} {tx.currency} لخطة {plan.name}",
            entity_type="payment_transaction",
            entity_id=tx.id,
        )


async def check_payment_status(db: AsyncSession, session_id: str, host_url: str) -> dict:
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = _stripe_client(webhook_url)
    status_resp: CheckoutStatusResponse = await stripe_checkout.get_checkout_status(session_id)

    tx = await payment_transactions_repo.get_by_session_id(db, session_id)
    if tx and tx.payment_status != "paid" and status_resp.payment_status == "paid":
        tx.payment_status = status_resp.payment_status
        tx.status = status_resp.status
        await _activate_from_transaction(db, tx)

    return {
        "status": status_resp.status, "payment_status": status_resp.payment_status,
        "amount_total": status_resp.amount_total, "currency": status_resp.currency,
    }


async def stripe_webhook(db: AsyncSession, body: bytes, signature: str, host_url: str) -> dict:
    webhook_url = f"{host_url}/api/webhook/stripe"
    stripe_checkout = _stripe_client(webhook_url)
    webhook_response = await stripe_checkout.handle_webhook(body, signature)

    if webhook_response.session_id:
        tx = await payment_transactions_repo.get_by_session_id(db, webhook_response.session_id)
        if tx and tx.payment_status != "paid" and webhook_response.payment_status == "paid":
            tx.payment_status = webhook_response.payment_status
            await _activate_from_transaction(db, tx)

    return {"status": "received"}


async def get_public_subscription_plans(db: AsyncSession, current_user: dict) -> list:
    plans = await subscription_plans_repo.list_active(db)
    return [plan_to_response(p) for p in plans]


async def get_owner_subscription(db: AsyncSession, current_user: dict) -> dict:
    if current_user["role"] != "company_owner":
        raise HTTPException(status_code=403, detail="Access denied")
    company = await companies_repo.get_by_id(db, parse_uuid(current_user["company_id"]))
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")

    plan = await subscription_plans_repo.get_by_id(db, company.subscription_plan_id) if company.subscription_plan_id else None
    return {
        "subscription_status": company.subscription_status,
        "subscription_end_date": company.subscription_end_date.isoformat() if company.subscription_end_date else None,
        "current_plan": plan_to_response(plan) if plan else None,
    }
