"""add users and per-user ownership of people

Revision ID: a1b2c3d4e5f6
Revises: f24600fd7608
Create Date: 2026-09-28 12:00:00

Existing rows keep people.user_id = NULL; the first account created after this
migration adopts them (see signup in backend/main.py).
"""
from alembic import op
import sqlalchemy as sa

revision = "a1b2c3d4e5f6"
down_revision = "f24600fd7608"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "users",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("email", sa.String(length=255), nullable=False),
        sa.Column("password_hash", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_users_email", "users", ["email"], unique=True)

    with op.batch_alter_table("people") as batch:
        batch.add_column(sa.Column("user_id", sa.Integer(), nullable=True))
        batch.create_index("ix_people_user_id", ["user_id"])
        batch.create_foreign_key("fk_people_user_id", "users", ["user_id"], ["id"], ondelete="CASCADE")


def downgrade() -> None:
    with op.batch_alter_table("people") as batch:
        batch.drop_constraint("fk_people_user_id", type_="foreignkey")
        batch.drop_index("ix_people_user_id")
        batch.drop_column("user_id")
    op.drop_index("ix_users_email", table_name="users")
    op.drop_table("users")
