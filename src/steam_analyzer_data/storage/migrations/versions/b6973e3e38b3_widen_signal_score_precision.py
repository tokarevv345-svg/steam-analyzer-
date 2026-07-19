"""widen signal score precision to avoid overflow on arbitrage profit_pct

Revision ID: b6973e3e38b3
Revises: 7d3a19f4b8c2
Create Date: 2026-07-19 00:00:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "b6973e3e38b3"
down_revision: Union[str, None] = "7d3a19f4b8c2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Numeric(6, 4) хватало на FLIP-score (взвешенная сумма небольших
    # компонентов), но ARBITRAGE пишет сюда "сырой" profit_pct без верхней
    # границы — переполнение (numeric field overflow) уже на скромном
    # арбитраже. Найдено ревью 19.07.2026 перед мержем в master.
    op.alter_column(
        "signals",
        "score",
        type_=sa.Numeric(precision=10, scale=4),
        existing_type=sa.Numeric(precision=6, scale=4),
    )


def downgrade() -> None:
    op.alter_column(
        "signals",
        "score",
        type_=sa.Numeric(precision=6, scale=4),
        existing_type=sa.Numeric(precision=10, scale=4),
    )
