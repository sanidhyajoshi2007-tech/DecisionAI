"""add_security_and_privacy_features

Revision ID: e71b2a9d4f10
Revises: d98ac9cff3be
Create Date: 2026-10-23 22:30:00.000000

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'e71b2a9d4f10'
down_revision: Union[str, Sequence[str], None] = 'd98ac9cff3be'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema with audit_logs table, retention_days, and consent_recorded."""
    conn = op.get_bind()
    inspector = sa.inspect(conn)
    tables = inspector.get_table_names()

    # 1. Audit logs table
    if 'audit_logs' not in tables:
        op.create_table(
            'audit_logs',
            sa.Column('id', sa.Integer(), primary_key=True, autoincrement=True),
            sa.Column('user_id', sa.Integer(), nullable=True),
            sa.Column('action', sa.String(length=100), nullable=False),
            sa.Column('target', sa.String(length=500), nullable=True),
            sa.Column('timestamp', sa.DateTime(timezone=True), nullable=False),
            sa.Column('ip', sa.String(length=100), nullable=True),
        )
        op.create_index(op.f('ix_audit_logs_user_id'), 'audit_logs', ['user_id'], unique=False)
        op.create_index(op.f('ix_audit_logs_action'), 'audit_logs', ['action'], unique=False)
        op.create_index(op.f('ix_audit_logs_timestamp'), 'audit_logs', ['timestamp'], unique=False)

    # 2. User retention_days
    user_cols = [c['name'] for c in inspector.get_columns('users')]
    if 'retention_days' not in user_cols:
        op.add_column('users', sa.Column('retention_days', sa.Integer(), nullable=True))

    # 3. Meeting consent_recorded
    meeting_cols = [c['name'] for c in inspector.get_columns('meetings')]
    if 'consent_recorded' not in meeting_cols:
        op.add_column('meetings', sa.Column('consent_recorded', sa.Boolean(), server_default='0', nullable=False))


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('meetings', 'consent_recorded')
    op.drop_column('users', 'retention_days')
    op.drop_index(op.f('ix_audit_logs_timestamp'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_action'), table_name='audit_logs')
    op.drop_index(op.f('ix_audit_logs_user_id'), table_name='audit_logs')
    op.drop_table('audit_logs')
