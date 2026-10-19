"""artifact_blobs table (ARTIFACT_STORE=database)

Revision ID: 0002
Revises: 0001
"""
import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table("artifact_blobs", sa.Column("key", sa.String(400), primary_key=True),
                    sa.Column("data", sa.LargeBinary(), nullable=False))


def downgrade():
    op.drop_table("artifact_blobs")
