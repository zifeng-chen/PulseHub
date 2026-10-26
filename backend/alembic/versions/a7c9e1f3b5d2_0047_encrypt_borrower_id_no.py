"""0047 借款人身份证号改为加密存储

- 列宽 64 → 255（Fernet 密文长度约 120）
- 历史明文逐行加密；已是密文（gAAAAA 前缀）的行跳过，保证重复执行安全

Revision ID: a7c9e1f3b5d2
Revises: e7b1d5c28f03
Create Date: 2026-10-22
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "a7c9e1f3b5d2"
down_revision = "e7b1d5c28f03"
branch_labels = None
depends_on = None

TABLE = "asset_receivables"


def _rows(conn):
    return conn.execute(sa.text(
        f"SELECT id, borrower_id_no FROM {TABLE} "
        "WHERE borrower_id_no IS NOT NULL AND borrower_id_no <> ''"
    )).fetchall()


def upgrade() -> None:
    from app.core.crypto import encrypt_secret

    with op.batch_alter_table(TABLE) as batch:
        batch.alter_column(
            "borrower_id_no",
            existing_type=sa.String(length=64),
            type_=sa.String(length=255),
            existing_nullable=True,
        )

    conn = op.get_bind()
    for row_id, value in _rows(conn):
        if not value or value.startswith("gAAAAA"):
            continue
        conn.execute(
            sa.text(f"UPDATE {TABLE} SET borrower_id_no = :value WHERE id = :id"),
            {"value": encrypt_secret(value), "id": row_id},
        )


def downgrade() -> None:
    from app.core.crypto import decrypt_secret

    conn = op.get_bind()
    for row_id, value in _rows(conn):
        plain = decrypt_secret(value) if value and value.startswith("gAAAAA") else value
        if plain and plain != value:
            conn.execute(
                sa.text(f"UPDATE {TABLE} SET borrower_id_no = :value WHERE id = :id"),
                {"value": plain, "id": row_id},
            )

    with op.batch_alter_table(TABLE) as batch:
        batch.alter_column(
            "borrower_id_no",
            existing_type=sa.String(length=255),
            type_=sa.String(length=64),
            existing_nullable=True,
        )
