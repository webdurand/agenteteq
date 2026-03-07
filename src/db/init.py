import logging

from src.db.models import Base
from src.db.session import get_engine

logger = logging.getLogger(__name__)


def ensure_tables():
    """Create all tables defined in ORM models (idempotent)."""
    try:
        engine = get_engine()
        Base.metadata.create_all(engine)
        logger.info("[DB] All tables ensured.")
    except Exception as e:
        logger.error("[DB] Failed to create tables: %s", e)
        raise
