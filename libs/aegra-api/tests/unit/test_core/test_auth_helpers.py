"""Unit tests for tenant ownership helpers (AE-568)."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from aegra_api.core.auth_helpers import (
    apply_ownership_filter,
    deny_if_not_owner,
    is_admin,
)
from aegra_api.core.orm import Thread as ThreadORM


class TestIsAdmin:
    def test_admin_permission_returns_true(self):
        user = SimpleNamespace(identity="1", permissions=["admin"])
        assert is_admin(user) is True

    def test_no_permission_returns_false(self):
        user = SimpleNamespace(identity="42", permissions=[])
        assert is_admin(user) is False

    def test_unrelated_permission_returns_false(self):
        user = SimpleNamespace(identity="42", permissions=["read", "write"])
        assert is_admin(user) is False

    def test_missing_permissions_attr_returns_false(self):
        user = SimpleNamespace(identity="42")
        assert is_admin(user) is False

    def test_none_permissions_returns_false(self):
        user = SimpleNamespace(identity="42", permissions=None)
        assert is_admin(user) is False


class TestApplyOwnershipFilter:
    def test_admin_passthrough_no_user_filter(self):
        """Admin: stmt returned unchanged, no user_id WHERE clause appended."""
        admin = SimpleNamespace(identity="1", permissions=["admin"])
        base_stmt = select(ThreadORM).where(ThreadORM.thread_id == "abc")
        result = apply_ownership_filter(base_stmt, admin, ThreadORM)

        compiled = str(result.compile(compile_kwargs={"literal_binds": True}))
        # Assert on the WHERE clause only: user_id also appears in the SELECT
        # projection, so a substring check over the whole statement is meaningless.
        where_clause = compiled.split("WHERE", 1)[1]
        assert "user_id" not in where_clause

    def test_non_admin_appends_user_filter(self):
        """Regular user: stmt gains user_id == identity WHERE clause."""
        user = SimpleNamespace(identity="42", permissions=[])
        base_stmt = select(ThreadORM).where(ThreadORM.thread_id == "abc")
        result = apply_ownership_filter(base_stmt, user, ThreadORM)

        compiled = str(result.compile(compile_kwargs={"literal_binds": True}))
        assert "user_id" in compiled
        assert "'42'" in compiled

    def test_admin_passthrough_returns_same_object(self):
        """Identity-comparison: admin path returns the input statement
        without wrapping/copying — relevant for callers that chain further
        builder operations after ownership."""
        admin = SimpleNamespace(identity="1", permissions=["admin"])
        base_stmt = select(ThreadORM)
        result = apply_ownership_filter(base_stmt, admin, ThreadORM)
        assert result is base_stmt


class TestDenyIfNotOwner:
    def test_owner_passes(self):
        user = SimpleNamespace(identity="42", permissions=[])
        thread = SimpleNamespace(user_id="42")
        deny_if_not_owner(thread, user, "abc")  # no raise

    def test_other_user_404(self):
        user = SimpleNamespace(identity="42", permissions=[])
        thread = SimpleNamespace(user_id="23")
        with pytest.raises(HTTPException) as exc:
            deny_if_not_owner(thread, user, "abc")
        assert exc.value.status_code == 404

    def test_admin_bypass(self):
        admin = SimpleNamespace(identity="1", permissions=["admin"])
        thread = SimpleNamespace(user_id="23")
        deny_if_not_owner(thread, admin, "abc")  # no raise

    def test_none_thread_passes(self):
        """Non-existent thread propagates to caller; helper does not raise."""
        user = SimpleNamespace(identity="42", permissions=[])
        deny_if_not_owner(None, user, "abc")  # no raise
