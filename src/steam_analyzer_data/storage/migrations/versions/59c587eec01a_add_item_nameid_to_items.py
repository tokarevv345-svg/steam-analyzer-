"""add item_nameid to items

Revision ID: 59c587eec01a
Revises: c80ab7cf61a1
Create Date: 2026-07-17 20:05:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "59c587eec01a"
down_revision: Union[str, None] = "c80ab7cf61a1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("items", sa.Column("item_nameid", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("items", "item_nameid")
