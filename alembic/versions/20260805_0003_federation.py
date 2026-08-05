"""Federation tables + api_keys.scopes.

Revision ID: 20260805_0003
Revises: 20260804_0002
Create Date: 2026-08-05

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260805_0003"
down_revision: Union[str, None] = "20260804_0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "api_keys",
        sa.Column("scopes", sa.String(length=512), nullable=False, server_default=""),
    )

    op.create_table(
        "federation_peers",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("label", sa.String(length=128), nullable=False),
        sa.Column("base_url", sa.String(length=512), nullable=False),
        sa.Column("public_browse_url", sa.String(length=512), nullable=True),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_federation_peers_label", "federation_peers", ["label"])

    op.create_table(
        "federation_mounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("prefix", sa.String(length=128), nullable=False),
        sa.Column("peer_id", sa.Uuid(), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("shadow_policy", sa.String(length=16), nullable=False),
        sa.Column("max_hops_override", sa.Integer(), nullable=True),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("notes", sa.String(length=500), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["peer_id"], ["federation_peers.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("prefix", name="uq_federation_mount_prefix"),
    )
    op.create_index("ix_federation_mounts_prefix", "federation_mounts", ["prefix"])
    op.create_index("ix_federation_mounts_peer_id", "federation_mounts", ["peer_id"])

    op.create_table(
        "federation_credentials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("peer_id", sa.Uuid(), nullable=False),
        sa.Column("mount_id", sa.Uuid(), nullable=True),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("ciphertext", sa.String(length=4096), nullable=False),
        sa.Column("fingerprint", sa.String(length=32), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("rotated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["peer_id"], ["federation_peers.id"]),
        sa.ForeignKeyConstraint(["mount_id"], ["federation_mounts.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_federation_credentials_peer_id", "federation_credentials", ["peer_id"])
    op.create_index("ix_federation_credentials_mount_id", "federation_credentials", ["mount_id"])

    op.create_table(
        "federation_grants",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("prefix", sa.String(length=128), nullable=False),
        sa.Column("parent_label", sa.String(length=128), nullable=False),
        sa.Column("parent_base_url", sa.String(length=512), nullable=True),
        sa.Column("parent_public_key", sa.String(length=2048), nullable=True),
        sa.Column("api_key_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("revoked_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["api_key_id"], ["api_keys.id"]),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("ix_federation_grants_prefix", "federation_grants", ["prefix"])


def downgrade() -> None:
    op.drop_table("federation_grants")
    op.drop_table("federation_credentials")
    op.drop_table("federation_mounts")
    op.drop_table("federation_peers")
    op.drop_column("api_keys", "scopes")
