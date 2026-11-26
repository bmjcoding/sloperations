"""Create audit events."""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "audit_events",
        sa.Column("id", sa.BigInteger, primary_key=True),
        sa.Column("account_id", sa.Integer, sa.ForeignKey("accounts.id"), nullable=False),
        sa.Column("action", sa.Text, nullable=False),
    )
    op.create_index("audit_events_account", "audit_events", ["account_id"])


def downgrade():
    op.drop_table("audit_events")
