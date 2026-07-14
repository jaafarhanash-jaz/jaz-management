import uuid

from sqlalchemy.ext.asyncio import AsyncSession

from models import Notification


async def create(db: AsyncSession, *, user_id, company_id, type: str, title: str, message: str) -> Notification:
    notification = Notification(
        id=uuid.uuid4(),
        user_id=user_id,
        company_id=company_id,
        type=type,
        title=title,
        message=message,
        read_status=False,
    )
    db.add(notification)
    await db.flush()
    return notification
