"""Tests for parser keyword matching and deduplication logic."""
from __future__ import annotations

import pytest

from database.models import Category, CategoryType


def _first_matched_category(text: str, categories: list[Category]) -> Category | None:
    """Pure function extracted from parser manager._handle_message."""
    text_lower = text.lower()
    for cat in categories:
        for kw in cat.get_keywords():
            if kw and kw in text_lower:
                return cat
    return None


def make_cat(name: str, keywords: str, cat_type=CategoryType.request) -> Category:
    return Category(name=name, type=cat_type, keywords=keywords, is_active=True)


class TestKeywordMatching:
    def test_matches_single_keyword(self):
        cats = [make_cat("Дизайн", "логотип,баннер")]
        assert _first_matched_category("Нужен логотип для компании", cats) is not None

    def test_matches_keyword_in_middle_of_word(self):
        cats = [make_cat("Дизайн", "дизайн")]
        result = _first_matched_category("Ищу дизайнера", cats)
        assert result is not None  # "дизайн" is in "дизайнера"

    def test_no_match_returns_none(self):
        cats = [make_cat("Дизайн", "логотип,баннер")]
        assert _first_matched_category("Ищу сантехника", cats) is None

    def test_case_insensitive_match(self):
        cats = [make_cat("Дизайн", "логотип")]
        assert _first_matched_category("Нужен ЛОГОТИП срочно", cats) is not None

    def test_first_matching_category_returned(self):
        cat1 = make_cat("Дизайн", "логотип")
        cat2 = make_cat("Разработка", "сайт")
        cats = [cat1, cat2]
        result = _first_matched_category("Нужен логотип и сайт", cats)
        assert result.name == "Дизайн"

    def test_empty_keywords_never_match(self):
        cats = [make_cat("Пустая", "")]
        assert _first_matched_category("любой текст", cats) is None

    def test_multi_word_keyword_matches(self):
        cats = [make_cat("Разработка", "разработка сайта")]
        assert _first_matched_category("Ищу специалиста по разработка сайта под ключ", cats) is not None

    def test_multi_word_keyword_no_partial_match(self):
        cats = [make_cat("Разработка", "разработка сайта")]
        assert _first_matched_category("Нужна разработка приложения", cats) is None

    def test_inactive_category_can_still_match(self):
        """Parser manager filters active categories before calling; pure matching doesn't filter."""
        cat = make_cat("Дизайн", "логотип")
        cat.is_active = False
        assert _first_matched_category("нужен логотип", [cat]) is not None

    def test_multiple_keywords_in_message(self):
        cats = [make_cat("Дизайн", "логотип,баннер,флаер")]
        result = _first_matched_category("нужен флаер и баннер", cats)
        assert result is not None

    def test_keyword_with_spaces_stripped(self):
        cats = [make_cat("Дизайн", " логотип , баннер ")]
        assert _first_matched_category("нужен логотип", cats) is not None

    def test_both_category_types_matched(self):
        req_cat = make_cat("Запрос дизайн", "логотип", CategoryType.request)
        off_cat = make_cat("Предложение", "предлагаю", CategoryType.offer)
        cats = [req_cat, off_cat]

        assert _first_matched_category("нужен логотип", cats).type == CategoryType.request
        assert _first_matched_category("предлагаю услуги", cats).type == CategoryType.offer


class TestDeduplicationLogic:
    async def test_message_id_not_in_db_is_new(self, session):
        from sqlalchemy import select
        from database.models import ParsedMessage

        result = await session.execute(
            select(ParsedMessage).where(
                ParsedMessage.group_id == 999,
                ParsedMessage.message_id == 1,
            )
        )
        assert result.scalar_one_or_none() is None

    async def test_after_insert_message_is_known(self, session):
        from sqlalchemy import select
        from database.models import ParsedMessage

        pm = ParsedMessage(group_id=999, message_id=1, text="hello")
        session.add(pm)
        await session.commit()

        result = await session.execute(
            select(ParsedMessage).where(
                ParsedMessage.group_id == 999,
                ParsedMessage.message_id == 1,
            )
        )
        assert result.scalar_one_or_none() is not None

    async def test_duplicate_insert_blocked(self, session):
        from sqlalchemy import select
        from sqlalchemy.exc import IntegrityError
        from database.models import ParsedMessage

        pm1 = ParsedMessage(group_id=999, message_id=2, text="orig")
        session.add(pm1)
        await session.commit()

        pm2 = ParsedMessage(group_id=999, message_id=2, text="dup")
        session.add(pm2)
        with pytest.raises(IntegrityError):
            await session.commit()
