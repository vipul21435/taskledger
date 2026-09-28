"""Alembic environment: runs on the connection that ``Ledger.migrate`` passes in."""

from __future__ import annotations

from alembic import context
from sqlalchemy.engine import Connection

from taskledger.ledger.models import Base

connection = context.config.attributes.get("connection")
if not isinstance(connection, Connection):
    raise RuntimeError("run migrations through taskledger.ledger.Ledger.migrate()")

context.configure(
    connection=connection,
    target_metadata=Base.metadata,
    render_as_batch=connection.dialect.name == "sqlite",
)
with context.begin_transaction():
    context.run_migrations()
