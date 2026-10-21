"""add stt segment fields

Revision ID: f174413c4f69
Revises: 046d271ddb33
Create Date: 2026-10-21 21:36:05.001311

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = 'f174413c4f69'
down_revision: Union[str, Sequence[str], None] = '046d271ddb33'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema: add raw_text, start_ms, and end_ms to transcript_segments."""
    op.add_column('transcript_segments', sa.Column('raw_text', sa.Text(), nullable=True))
    op.add_column('transcript_segments', sa.Column('start_ms', sa.Integer(), nullable=True))
    op.add_column('transcript_segments', sa.Column('end_ms', sa.Integer(), nullable=True))
    op.execute("UPDATE transcript_segments SET raw_text = text WHERE raw_text IS NULL")
    op.execute("UPDATE transcript_segments SET start_ms = CAST(start_time * 1000 AS INTEGER) WHERE start_ms IS NULL")
    op.execute("UPDATE transcript_segments SET end_ms = CAST(end_time * 1000 AS INTEGER) WHERE end_ms IS NULL")


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column('transcript_segments', 'end_ms')
    op.drop_column('transcript_segments', 'start_ms')
    op.drop_column('transcript_segments', 'raw_text')
