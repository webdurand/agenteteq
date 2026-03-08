import logging

from sqlalchemy import inspect, text
from src.db.models import Base
from src.db.session import get_engine

logger = logging.getLogger(__name__)


def _add_missing_columns(engine):
    """Add columns that exist in ORM models but not in the database."""
    inspector = inspect(engine)
    for table_name, table in Base.metadata.tables.items():
        if not inspector.has_table(table_name):
            continue
        existing = {col["name"] for col in inspector.get_columns(table_name)}
        for column in table.columns:
            if column.name not in existing:
                col_type = column.type.compile(engine.dialect)
                with engine.begin() as conn:
                    conn.execute(text(
                        f'ALTER TABLE "{table_name}" ADD COLUMN "{column.name}" {col_type}'
                    ))
                logger.info("[DB] Added column %s.%s (%s)", table_name, column.name, col_type)


def ensure_tables():
    """Create all tables defined in ORM models (idempotent)."""
    try:
        engine = get_engine()
        Base.metadata.create_all(engine)
        _add_missing_columns(engine)
        logger.info("[DB] All tables ensured.")
    except Exception as e:
        logger.error("[DB] Failed to create tables: %s", e)
        raise
