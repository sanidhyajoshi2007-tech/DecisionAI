"""
Audit logging service.
Records operational and security audit events for user actions.
Strict privacy constraint: NEVER log transcript text, question content, tokens, or API keys.
Strict style constraint: No emojis, no em dashes.
"""

from fastapi import Request
from sqlalchemy.ext.asyncio import AsyncSession

from backend.models.audit_log import AuditLog


def get_client_ip(request: Request | None) -> str | None:
    """Extract client IP address safely from request headers or connection host."""
    if not request:
        return None
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    if request.client:
        return request.client.host
    return None


async def log_audit_event(
    db: AsyncSession,
    action: str,
    user_id: int | None = None,
    target: str | None = None,
    ip: str | None = None,
    request: Request | None = None,
) -> AuditLog:
    """
    Persist an audit log entry.
    Ensures safe recording of security actions without sensitive payloads.
    """
    resolved_ip = ip or get_client_ip(request)
    entry = AuditLog(
        user_id=user_id,
        action=action,
        target=target,
        ip=resolved_ip,
    )
    db.add(entry)
    await db.flush()
    return entry
