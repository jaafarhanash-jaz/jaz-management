import base64
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO

import qrcode
from sqlalchemy.ext.asyncio import AsyncSession

import repositories.subscription_plans as plans_repo
import repositories.users as users_repo
from models import Company
from services.auth import hash_password


def _generate_qr_code(data: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"


async def seed_identity_data(db: AsyncSession) -> None:
    """Postgres-backed portion of /api/seed: super admin, the demo
    subscription plan, the demo company+owner (mutual deferred FK, resolved
    at commit), and the two demo employees. Idempotent via business keys
    (email / plan name) rather than the old fixed literal ids, since ids are
    now real UUIDs generated fresh on first creation (plan decision #1).

    Departments/tasks demo data stay on the old Mongo-backed code path in
    server.py until the Employees/Tasks modules migrate - not touched here.
    """
    admin = await users_repo.get_by_email(db, "admin@jaz.com")
    if not admin:
        await users_repo.create(
            db,
            email="admin@jaz.com", phone="0500000000",
            password=hash_password("admin123"), name="Super Admin",
            role="super_admin", status="active",
        )
        await db.commit()

    plan = await plans_repo.get_by_name(db, "خطة صغيرة")
    if not plan:
        plan = await plans_repo.create(
            db,
            name="خطة صغيرة", max_employees=10, price=99.0, duration_months=1,
            features=["إدارة الموظفين", "نظام الحضور", "إدارة المهام"], is_active=True,
        )
        await db.commit()

    owner = await users_repo.get_by_email(db, "owner@demo.com")
    if not owner:
        owner_id = uuid.uuid4()
        company_id = uuid.uuid4()
        qr_code = _generate_qr_code(f"company:{company_id}")

        owner = await users_repo.create(
            db,
            id=owner_id,
            email="owner@demo.com", phone="0501111111",
            password=hash_password("owner123"), name="أحمد محمد",
            role="company_owner", company_id=company_id, status="active",
        )
        company = Company(
            id=company_id,
            name="شركة النجاح",
            owner_id=owner_id,
            qr_code=qr_code,
            subscription_status="active",
            subscription_plan_id=plan.id,
            subscription_end_date=datetime.now(timezone.utc) + timedelta(days=30),
            address="الرياض، المملكة العربية السعودية",
        )
        db.add(company)
        await db.flush()

        await users_repo.create(
            db,
            email="employee1@demo.com", phone="0502222222",
            password=hash_password("emp123"), name="فاطمة أحمد",
            role="employee", company_id=company_id,
            department="المبيعات", position="مندوب مبيعات", status="active",
        )
        await users_repo.create(
            db,
            email="employee2@demo.com", phone="0503333333",
            password=hash_password("emp123"), name="محمد خالد",
            role="employee", company_id=company_id,
            department="التسويق", position="مسوق رقمي", status="active",
        )
        await db.commit()
