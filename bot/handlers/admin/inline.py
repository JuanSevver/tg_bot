"""Inline mode handler for admin user search."""
from __future__ import annotations

from aiogram import Router
from aiogram.types import InlineQuery, InlineQueryResultArticle, InputTextMessageContent
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from config import load_config
from database.models import User

router = Router(name="admin_inline")
_config = load_config()


@router.inline_query()
async def inline_users(query: InlineQuery, session: AsyncSession) -> None:
    # Only admins can use inline search
    if query.from_user.id not in _config.admin_ids:
        await query.answer([], cache_time=1)
        return

    search = query.query.strip().lstrip("@")
    if not search:
        await query.answer([], cache_time=1, switch_pm_text="Введите username или ID", switch_pm_parameter="start")
        return

    # Search by username or telegram ID
    try:
        uid = int(search)
        cond = or_(User.id == uid, User.username.ilike(f"%{search}%"))
    except ValueError:
        cond = User.username.ilike(f"%{search}%")

    result = await session.execute(select(User).where(cond).limit(20))
    users = result.scalars().all()

    items = []
    for user in users:
        username_str = f"@{user.username}" if user.username else "—"
        sub_status = "✅" if (user.subscription and user.subscription.is_active) else "❌"
        title = f"{user.full_name} ({username_str})"
        description = f"ID: {user.id} | Подписка: {sub_status} | Сообщений: {user.messages_received}"
        message_text = (
            f"👤 <b>{user.full_name}</b>\n"
            f"🆔 ID: <code>{user.id}</code>\n"
            f"💬 Username: {username_str}\n"
            f"💳 Подписка: {sub_status}\n"
            f"📨 Получено заявок: <b>{user.messages_received}</b>"
        )
        items.append(
            InlineQueryResultArticle(
                id=str(user.id),
                title=title,
                description=description,
                input_message_content=InputTextMessageContent(
                    message_text=message_text,
                    parse_mode="HTML",
                ),
            )
        )

    await query.answer(items, cache_time=5, is_personal=True)
