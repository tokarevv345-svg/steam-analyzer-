"""add arbitrage signal type and buy-order data to price_snapshots

Revision ID: 7d3a19f4b8c2
Revises: 59c587eec01a
Create Date: 2026-07-17 20:40:00.000000

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "7d3a19f4b8c2"
down_revision: Union[str, None] = "59c587eec01a"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ALTER TYPE ... ADD VALUE безопасен внутри обычной транзакции начиная с
    # Postgres 12, при условии что новое значение не используется в этой же
    # транзакции (здесь — не используется, только добавляется).
    #
    # Идемпотентность: Postgres не поддерживает ALTER TYPE ... DROP VALUE,
    # поэтому downgrade() этой же миграции не может убрать 'ARBITRAGE' обратно
    # (см. комментарий в downgrade). Если БД когда-либо проходила upgrade и
    # downgrade этой миграции, значение 'ARBITRAGE' в enum остаётся навсегда,
    # и повторный upgrade падает с DuplicateObject. Проверяем через pg_enum
    # перед ALTER TYPE, а не оборачиваем в DO $$ ... EXCEPTION $$ — у ALTER
    # TYPE ... ADD VALUE есть собственные ограничения на выполнение внутри
    # PL/pgSQL-блоков, конфликтующие с текущей транзакцией.
    bind = op.get_bind()
    already_exists = bind.execute(
        sa.text(
            "SELECT 1 FROM pg_enum WHERE enumlabel = 'ARBITRAGE' "
            "AND enumtypid = 'signal_type'::regtype"
        )
    ).scalar()
    if not already_exists:
        op.execute("ALTER TYPE signal_type ADD VALUE 'ARBITRAGE'")

    op.add_column(
        "price_snapshots",
        sa.Column("highest_buy_order", sa.Numeric(precision=10, scale=2), nullable=True),
    )
    op.add_column(
        "price_snapshots",
        sa.Column("buy_order_count", sa.Integer(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("price_snapshots", "buy_order_count")
    op.drop_column("price_snapshots", "highest_buy_order")
    # Postgres не поддерживает ALTER TYPE ... DROP VALUE — откат enum
    # потребовал бы пересоздания типа signal_type целиком (переименовать,
    # создать заново без ARBITRAGE, перелить данные, удалить старый). Не
    # делаем это автоматически: если понадобится полный откат — отдельная
    # ручная операция, а не часть downgrade().
