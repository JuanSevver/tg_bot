from __future__ import annotations

from aiogram import Router, F
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from bot.keyboards import groups_list_kb, cancel_kb
from bot.states import GroupSG
from database.models import TelegramGroup

router = Router(name="admin_groups")


@router.callback_query(F.data == "adm:groups")
async def cb_groups(callback: CallbackQuery, state: FSMContext, session: AsyncSession) -> None:
    result = await session.execute(select(TelegramGroup).order_by(TelegramGroup.added_at.desc()))
    groups = result.scalars().all()
    await state.set_state(GroupSG.list)
    await callback.message.edit_text(
        f"🔗 <b>Группы/каналы</b> ({len(groups)})\n\nНажмите на группу для вкл/выкл:",
        reply_markup=groups_list_kb(list(groups)),
        parse_mode="HTML",
    )
    await callback.answer()


@router.callback_query(F.data.startswith("adm:grp:toggle:"))
async def cb_group_toggle(callback: CallbackQuery, session: AsyncSession) -> None:
    group_id = int(callback.data.split(":")[-1])
    result = await session.execute(select(TelegramGroup).where(TelegramGroup.id == group_id))
    group = result.scalar_one_or_none()
    if group:
        group.is_active = not group.is_active
        await session.commit()
    result2 = await session.execute(select(TelegramGroup).order_by(TelegramGroup.added_at.desc()))
    groups = result2.scalars().all()
    await callback.message.edit_reply_markup(reply_markup=groups_list_kb(list(groups)))
    await callback.answer()


@router.callback_query(F.data == "adm:grp:add")
async def cb_group_add(callback: CallbackQuery, state: FSMContext) -> None:
    await state.set_state(GroupSG.add_link)
    await callback.message.edit_text(
        "Введите ссылку на группу/канал\n(например: <code>https://t.me/example</code> или <code>@example</code>):",
        reply_markup=cancel_kb("adm:groups"),
        parse_mode="HTML",
    )
    await callback.answer()


@router.message(GroupSG.add_link)
async def process_group_link(message: Message, state: FSMContext, session: AsyncSession) -> None:
    link = message.text.strip()
    result = await session.execute(select(TelegramGroup).where(TelegramGroup.link == link))
    existing = result.scalar_one_or_none()
    if existing:
        await message.answer("⚠️ Эта группа уже добавлена.", reply_markup=cancel_kb("adm:groups", "◀ К списку групп"))
        return

    group = TelegramGroup(link=link)
    session.add(group)
    await session.commit()

    result2 = await session.execute(select(TelegramGroup).order_by(TelegramGroup.added_at.desc()))
    groups = result2.scalars().all()
    await message.answer(
        f"✅ Группа <code>{link}</code> добавлена.\n\n"
        f"🔗 <b>Группы/каналы</b> ({len(groups)})",
        reply_markup=groups_list_kb(list(groups)),
        parse_mode="HTML",
    )
    await state.set_state(GroupSG.list)
