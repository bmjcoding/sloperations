"""Create accounts."""
import sqlalchemy as sa
from alembic import op

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "accounts",
        sa.Column("id", sa.Integer, primary_key=True),
        sa.Column("display_name", sa.Text, nullable=False),
        sa.Column("time_zone", sa.Text, nullable=False, server_default="UTC"),
        sa.Column("legacy_flag", sa.Boolean),
    )


def downgrade():
    op.drop_table("accounts")
