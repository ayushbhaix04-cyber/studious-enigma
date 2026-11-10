"""initial schema

Revision ID: 0001
Revises:
"""
from alembic import op

from database.models import Base

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Snapshot of the v1 schema. Every later change must be its own revision.
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind())
