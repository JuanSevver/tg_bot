"""
Multi-account parser manager using Telethon.

Responsibilities:
- Pool of Telethon clients (one per ParserAccount).
- Round-robin message history collection.
- Deduplication via ParsedMessage table.
- Deliver matched messages to subscribed users.
"""
from __future__ import annotations

import asyncio
import itertools
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.errors import (
    AuthKeyUnregisteredError, UserDeactivatedError, FloodWaitError,
    SessionPasswordNeededError,
)
from thefuzz import fuzz
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from database.db import async_session
from database.models import (
    ParserAccount, TelegramGroup, Category, UserCategory,
    ParsedMessage, User, Subscription,
)
from .client import make_client, proxy_tuple

if TYPE_CHECKING:
    from aiogram import Bot

logger = logging.getLogger(__name__)

# Порог схожести для однословных ключей и минус-слов
FUZZY_THRESHOLD = 82
# Порог схожести для многословных фраз (сравниваем всю фразу с окном текста)
PHRASE_THRESHOLD = 78


def _match_phrase(phrase: str, text: str) -> bool:
    """
    Проверяет наличие фразы в тексте.

    Однословная фраза:
      Нечёткое сравнение с каждым словом текста (token-level fuzzy).

    Многословная фраза:
      1. Сначала точное вхождение всей фразы как подстроки.
      2. Затем скользящее окно по тексту той же длины:
         сравниваем всю фразу с каждым окном целиком (phrase-level fuzzy).
         Это гарантирует что ВСЕ слова фразы должны быть рядом.
    """
    phrase = phrase.strip().lower()
    text = text.strip().lower()
    phrase_words = phrase.split()
    text_words = text.split()

    if not phrase_words or not text_words:
        return False

    # Однословная фраза — partial_ratio чтобы ловить словоформы (дизайн→дизайнер)
    if len(phrase_words) == 1:
        return any(fuzz.partial_ratio(phrase_words[0], w) >= FUZZY_THRESHOLD for w in text_words)

    # Многословная фраза

    # 1. Точное вхождение целой фразы
    if phrase in text:
        return True

    # 2. Скользящее окно: каждое слово фразы должно иметь fuzzy-пару в окне
    #    Окно = кол-во слов фразы + 3 (запас на вставленные слова между ключевыми)
    window_size = len(phrase_words) + 3
    for i in range(max(1, len(text_words) - window_size + 1)):
        window_words = text_words[i: i + window_size]
        if all(
            any(fuzz.ratio(pw, ww) >= FUZZY_THRESHOLD for ww in window_words)
            for pw in phrase_words
        ):
            return True

    return False


def _has_stop_word(stop_words: list[str], text: str) -> bool:
    """Возвращает True если в тексте найдено хотя бы одно минус-слово."""
    text_lower = text.lower()
    text_words = text_lower.split()
    for sw in stop_words:
        sw = sw.strip().lower()
        if not sw:
            continue
        # Точное вхождение
        if sw in text_lower:
            return True
        # Нечёткое — только для одиночных слов
        if " " not in sw and any(fuzz.ratio(sw, w) >= FUZZY_THRESHOLD for w in text_words):
            return True
    return False

# Temporary storage for pending sign-ins: {phone: (client, phone_code_hash)}
_pending: dict[str, tuple[TelegramClient, str]] = {}


class ParserManager:
    def __init__(self) -> None:
        self._clients: list[TelegramClient] = []
        self._cycle: itertools.cycle = itertools.cycle([])
        self._bot: "Bot | None" = None
        self._running = False

    def set_bot(self, bot: "Bot") -> None:
        self._bot = bot

    async def start(self) -> None:
        await self.reload_clients()
        self._running = True
        asyncio.create_task(self._polling_loop())

    async def stop(self) -> None:
        self._running = False
        for client in self._clients:
            try:
                await client.disconnect()
            except Exception:
                pass

    async def reload_clients(self) -> None:
        for c in self._clients:
            try:
                await c.disconnect()
            except Exception:
                pass
        self._clients.clear()

        async with async_session() as session:
            result = await session.execute(
                select(ParserAccount)
                .where(ParserAccount.is_active == True, ParserAccount.is_valid == True)
                .options(selectinload(ParserAccount.proxy))
            )
            accounts = result.scalars().all()

        for acc in accounts:
            if not acc.session_string:
                continue
            proxy = None
            if acc.proxy and acc.proxy.is_active:
                proxy = proxy_tuple(
                    acc.proxy.host, acc.proxy.port, acc.proxy.type,
                    acc.proxy.username, acc.proxy.password,
                )
            try:
                client = make_client(acc.session_string, proxy)
                await client.connect()
                if not await client.is_user_authorized():
                    raise AuthKeyUnregisteredError(request=None)
                self._clients.append(client)
                logger.info("Parser client account_%s started.", acc.id)
            except (AuthKeyUnregisteredError, UserDeactivatedError):
                async with async_session() as db:
                    r = await db.execute(select(ParserAccount).where(ParserAccount.id == acc.id))
                    a = r.scalar_one_or_none()
                    if a:
                        a.is_valid = False
                        await db.commit()
                logger.warning("Account %s invalid, marked.", acc.id)
            except Exception as e:
                logger.error("Failed to start client for account %s: %s", acc.id, e)

        self._cycle = itertools.cycle(self._clients) if self._clients else itertools.cycle([])

    # ------------------------------------------------------------------
    # Phone-based sign-in helpers
    # ------------------------------------------------------------------

    async def request_code(self, phone: str) -> str:
        from config import load_config
        cfg = load_config()
        client = TelegramClient(StringSession(), cfg.tg_api_id, cfg.tg_api_hash)
        await client.connect()
        result = await client.send_code_request(phone)
        _pending[phone] = (client, result.phone_code_hash)
        return result.phone_code_hash

    async def sign_in(self, phone: str, code: str, phone_code_hash: str) -> str:
        client, _ = _pending[phone]
        await client.sign_in(phone=phone, code=code, phone_code_hash=phone_code_hash)
        ss = client.session.save()
        await client.disconnect()
        _pending.pop(phone, None)
        return ss

    async def sign_in_2fa(self, phone: str, password: str) -> str:
        client, _ = _pending[phone]
        await client.sign_in(password=password)
        ss = client.session.save()
        await client.disconnect()
        _pending.pop(phone, None)
        return ss

    # ------------------------------------------------------------------
    # Polling loop
    # ------------------------------------------------------------------

    async def _polling_loop(self) -> None:
        while self._running:
            try:
                await self._collect_messages()
            except Exception as e:
                logger.error("Polling error: %s", e)
            await asyncio.sleep(30)

    async def _collect_messages(self) -> None:
        if not self._clients:
            return

        async with async_session() as session:
            groups_result = await session.execute(
                select(TelegramGroup).where(TelegramGroup.is_active == True)
            )
            groups = groups_result.scalars().all()

            cats_result = await session.execute(
                select(Category).where(Category.is_active == True)
            )
            categories = cats_result.scalars().all()

        if not groups or not categories:
            return

        for group in groups:
            client = next(self._cycle)
            try:
                await self._process_group(client, group, categories)
            except FloodWaitError as e:
                logger.warning("FloodWait %s sec for %s", e.seconds, group.link)
                await asyncio.sleep(e.seconds)
            except Exception as e:
                logger.error("Error processing group %s: %s", group.link, e)

    async def _process_group(
        self,
        client: TelegramClient,
        group: TelegramGroup,
        categories: list[Category],
    ) -> None:
        async with async_session() as session:
            try:
                async for message in client.iter_messages(group.link, limit=50):
                    if not message.text:
                        continue
                    await self._handle_message(session, message, group, categories)
            except Exception as e:
                logger.debug("Could not fetch history for %s: %s", group.link, e)

    async def _handle_message(self, session, message, group: TelegramGroup, categories: list[Category]) -> None:
        text_lower = (message.text or "").lower()

        check = await session.execute(
            select(ParsedMessage).where(
                ParsedMessage.group_id == message.chat_id,
                ParsedMessage.message_id == message.id,
            )
        )
        if check.scalar_one_or_none():
            return

        matched_cat = None
        for cat in categories:
            # Проверяем минус-слова — если есть, категория не подходит
            stop_words = cat.get_stop_words()
            if stop_words and _has_stop_word(stop_words, text_lower):
                continue

            # Проверяем ключевые фразы
            for phrase in cat.get_keywords():
                if _match_phrase(phrase, text_lower):
                    matched_cat = cat
                    break

            if matched_cat:
                break

        if not matched_cat:
            return

        sender = await message.get_sender()
        author_id = sender.id if sender else None
        author_username = getattr(sender, "username", None)
        author_link = f"https://t.me/{author_username}" if author_username else (
            f"tg://user?id={author_id}" if author_id else None
        )

        pm = ParsedMessage(
            group_id=message.chat_id,
            message_id=message.id,
            author_id=author_id,
            category_id=matched_cat.id,
            text=message.text,
            author_username=author_username,
            author_link=author_link,
        )
        session.add(pm)
        await session.commit()

        await self._deliver_message(pm, matched_cat)

    async def _deliver_message(self, pm: ParsedMessage, cat: Category) -> None:
        if not self._bot:
            return

        async with async_session() as session:
            result = await session.execute(
                select(User)
                .join(UserCategory, UserCategory.user_id == User.id)
                .join(Subscription, Subscription.user_id == User.id)
                .where(
                    User.receiving_enabled == True,
                    User.is_blocked == False,
                    UserCategory.category_id == cat.id,
                    UserCategory.enabled == True,
                    Subscription.expires_at > datetime.utcnow(),
                )
            )
            users = result.scalars().all()

            from bot.keyboards.user_kb import message_action_kb
            from aiogram.exceptions import TelegramRetryAfter, TelegramForbiddenError

            source = f"@{pm.author_username}" if pm.author_username else "Участник группы"
            text = (
                f"📨 <b>{cat.name}</b>\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n\n"
                f"{(pm.text or '')[:3500]}\n\n"
                f"━━━━━━━━━━━━━━━━━━━━━\n"
                f"👤 {source}"
            )
            kb = message_action_kb(pm.author_username, pm.author_link)

            for user in users:
                try:
                    await self._bot.send_message(
                        user.id, text, reply_markup=kb, parse_mode="HTML",
                    )
                    user.messages_received += 1
                except TelegramRetryAfter as e:
                    # Telegram просит подождать — соблюдаем
                    logger.warning("RetryAfter %s sec, pausing delivery.", e.retry_after)
                    await asyncio.sleep(e.retry_after)
                    try:
                        await self._bot.send_message(
                            user.id, text, reply_markup=kb, parse_mode="HTML",
                        )
                        user.messages_received += 1
                    except Exception:
                        pass
                except TelegramForbiddenError:
                    # Пользователь заблокировал бота — отключаем ему рассылку
                    user.receiving_enabled = False
                    logger.info("User %s blocked the bot, disabling delivery.", user.id)
                except Exception as e:
                    logger.debug("Deliver to user %s failed: %s", user.id, e)

                # Throttle: не более 25 сообщений/сек (лимит Telegram — 30/сек)
                await asyncio.sleep(0.04)

            await session.commit()


parser_manager = ParserManager()
