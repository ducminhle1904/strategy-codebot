"""add nautilus warming_up runtime state

Revision ID: 0010_nautilus_warming_up_state
Revises: 0009_nautilus_worker_plane
Create Date: 2026-06-27
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0010_nautilus_warming_up_state"
down_revision: str | None = "0009_nautilus_worker_plane"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

OLD_STATE_CHECK = "state IN ('requested','provisioning','running','degraded','stopping','stopped','failed')"
NEW_STATE_CHECK = "state IN ('requested','provisioning','warming_up','running','degraded','stopping','stopped','failed')"


def upgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("nautilus_runtimes") as batch_op:
            batch_op.drop_constraint("ck_nautilus_runtimes_state", type_="check")
            batch_op.create_check_constraint("ck_nautilus_runtimes_state", NEW_STATE_CHECK)
        return
    op.drop_constraint("ck_nautilus_runtimes_state", "nautilus_runtimes", type_="check")
    op.create_check_constraint("ck_nautilus_runtimes_state", "nautilus_runtimes", NEW_STATE_CHECK)


def downgrade() -> None:
    if op.get_bind().dialect.name == "sqlite":
        with op.batch_alter_table("nautilus_runtimes") as batch_op:
            batch_op.drop_constraint("ck_nautilus_runtimes_state", type_="check")
            batch_op.create_check_constraint("ck_nautilus_runtimes_state", OLD_STATE_CHECK)
        return
    op.drop_constraint("ck_nautilus_runtimes_state", "nautilus_runtimes", type_="check")
    op.create_check_constraint("ck_nautilus_runtimes_state", "nautilus_runtimes", OLD_STATE_CHECK)
