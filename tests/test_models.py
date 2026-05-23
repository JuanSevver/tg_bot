"""Tests for database model methods and properties."""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from database.models import (
    Category, CategoryType, Subscription, User, UserCategory,
)


class TestCategoryKeywords:
    def test_get_keywords_parses_comma_separated(self, make_category):
        cat = make_category(keywords="логотип,дизайн,баннер")
        assert cat.get_keywords() == ["логотип", "дизайн", "баннер"]

    def test_get_keywords_strips_spaces(self, make_category):
        cat = make_category(keywords=" лого , дизайн , ux ")
        assert cat.get_keywords() == ["лого", "дизайн", "ux"]

    def test_get_keywords_empty_string(self, make_category):
        cat = make_category(keywords="")
        assert cat.get_keywords() == []

    def test_get_keywords_skips_blank_entries(self, make_category):
        cat = make_category(keywords="лого,,дизайн,")
        assert cat.get_keywords() == ["лого", "дизайн"]

    def test_set_keywords_roundtrip(self, make_category):
        cat = make_category(keywords="")
        kws = ["дизайн", "логотип", "макет"]
        cat.set_keywords(kws)
        assert cat.get_keywords() == kws

    def test_set_keywords_empty_list(self, make_category):
        cat = make_category(keywords="дизайн")
        cat.set_keywords([])
        assert cat.get_keywords() == []

    def test_keyword_dedup_preserved_on_add(self, make_category):
        """Simulate add-keyword logic: merge keeps insertion order, no dups."""
        cat = make_category(keywords="дизайн,логотип")
        existing = cat.get_keywords()
        new_kws = ["логотип", "баннер"]
        merged = list(dict.fromkeys(existing + new_kws))
        cat.set_keywords(merged)
        assert cat.get_keywords() == ["дизайн", "логотип", "баннер"]


class TestSubscriptionProperties:
    def test_is_active_true_when_not_expired(self, make_subscription):
        sub = make_subscription(user_id=1, days=10)
        assert sub.is_active is True

    def test_is_active_false_when_expired(self, make_subscription):
        sub = make_subscription(user_id=1, days=-1)
        assert sub.is_active is False

    def test_days_left_positive(self, make_subscription):
        sub = make_subscription(user_id=1, days=30)
        # Allow 1-day margin for test speed
        assert 28 <= sub.days_left <= 30

    def test_days_left_zero_when_expired(self, make_subscription):
        sub = make_subscription(user_id=1, days=-5)
        assert sub.days_left == 0

    def test_purchases_count_not_incremented_for_trial(self, make_subscription):
        sub = make_subscription(user_id=1, plan="trial", purchases=0)
        assert sub.purchases_count == 0

    def test_plan_stored_correctly(self, make_subscription):
        for plan in ("trial", "1m", "3m", "1y"):
            sub = make_subscription(user_id=1, plan=plan)
            assert sub.plan == plan


class TestUserDefaults:
    def test_new_user_receiving_disabled(self, make_user):
        user = make_user()
        assert user.receiving_enabled is False

    def test_new_user_captcha_not_passed(self, make_user):
        user = make_user()
        assert user.captcha_passed is False

    def test_new_user_trial_not_used(self, make_user):
        user = make_user()
        assert user.trial_used is False

    def test_user_is_not_blocked_by_default(self, make_user):
        user = make_user()
        assert user.is_blocked is False

    def test_messages_received_zero_by_default(self, make_user):
        user = make_user()
        assert user.messages_received == 0
