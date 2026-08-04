"""Initial identity schema (users, ACL, orgs, audit).

Revision ID: 20260804_0001
Revises:
Create Date: 2026-08-04

"""

from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "20260804_0001"
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # create_all in app lifespan covers fresh DBs; this revision documents the
    # baseline for `metal-cdn db upgrade` / Alembic history.
    bind = op.get_bind()
    from sqlmodel import SQLModel

    import pymergetic.metal.cdn.models  # noqa: F401

    SQLModel.metadata.create_all(bind)


def downgrade() -> None:
    bind = op.get_bind()
    from sqlmodel import SQLModel

    import pymergetic.metal.cdn.models  # noqa: F401

    SQLModel.metadata.drop_all(bind)
