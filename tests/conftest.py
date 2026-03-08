"""
Shared fixtures for tests.
Uses SQLite in-memory for isolation — no external DB required.
"""
import os
import pytest

# Force SQLite for tests before any app import
os.environ["DATABASE_URL"] = "sqlite:///test.db"
os.environ["JWT_SECRET"] = "test-secret-key-for-jwt"
os.environ["OTP_EXPIRY_SECONDS"] = "120"

from src.db.models import Base
from src.db.session import get_engine


@pytest.fixture(autouse=True)
def setup_db():
    """Create all tables before each test, drop after."""
    engine = get_engine()
    Base.metadata.create_all(engine)
    yield
    Base.metadata.drop_all(engine)
