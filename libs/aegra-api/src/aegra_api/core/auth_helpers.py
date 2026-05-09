"""Tenant ownership helpers for ORM queries.

Centralizes the `WHERE user_id = user.identity` filter so that admin
identities (granted via aegra/auth.py via `permissions: ["admin"]`)
bypass the per-tenant scope while non-admin identities remain
constrained. ISO 27017 CLD.6.3.1 defense-in-depth: the filter is
applied at every query site, not just at the authorization handler
layer.

AE-568: introduced for super_admin cross-tenant read access. Audit
trail preserved via `permissions` field on structured access logs.
"""

from fastapi import HTTPException


def is_admin(user) -> bool:
    """Whether the authenticated user holds the `admin` permission."""
    return "admin" in (getattr(user, "permissions", None) or [])


def apply_ownership_filter(stmt, user, orm_class):
    """Conditionally enforce `orm_class.user_id == user.identity`.

    Returns the statement unchanged when the user is admin so callers
    can chain further filters without further branching. Non-admin
    callers always receive the additional ownership constraint.
    """
    if is_admin(user):
        return stmt
    return stmt.where(orm_class.user_id == user.identity)


def deny_if_not_owner(thread, user, thread_id: str) -> None:
    """Raise 404 if `thread` exists and is not owned by `user` (admins exempt).

    Mirrors the 404-on-mismatch contract of `apply_ownership_filter` for
    code paths that already loaded the thread via an unscoped query and
    need to gate access before proceeding.
    """
    if thread and thread.user_id != user.identity and not is_admin(user):
        raise HTTPException(404, f"Thread '{thread_id}' not found")
