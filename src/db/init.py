from sqlalchemy import text
from src.config.system_config import _get_pg_engine, _get_sqlite_conn, init_system_config_table

def ensure_tables():
    # system_config is handled by its own module
    init_system_config_table()
    
    engine = _get_pg_engine()
    if engine:
        with engine.connect() as conn:
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS image_sessions (
                    session_id TEXT NOT NULL,
                    image_type TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    image_index INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    PRIMARY KEY (session_id, image_type, image_index)
                )
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS background_tasks (
                    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
                    user_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    payload JSONB NOT NULL,
                    result JSONB,
                    attempts INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT NOW(),
                    updated_at TIMESTAMP DEFAULT NOW(),
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP
                )
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_bg_tasks_status ON background_tasks(status, created_at)
            """))
            conn.execute(text("""
                CREATE INDEX IF NOT EXISTS idx_bg_tasks_user ON background_tasks(user_id, status)
            """))
            conn.execute(text("""
                CREATE TABLE IF NOT EXISTS message_buffer (
                    user_id TEXT PRIMARY KEY,
                    events JSONB NOT NULL DEFAULT '[]',
                    flush_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT NOW()
                )
            """))
            conn.commit()
    else:
        with _get_sqlite_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS processed_messages (
                    message_id TEXT PRIMARY KEY,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS image_sessions (
                    session_id TEXT NOT NULL,
                    image_type TEXT NOT NULL,
                    image_url TEXT NOT NULL,
                    image_index INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    PRIMARY KEY (session_id, image_type, image_index)
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS background_tasks (
                    id TEXT PRIMARY KEY, -- SQLite UUID fallback
                    user_id TEXT NOT NULL,
                    task_type TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending',
                    payload TEXT NOT NULL, -- JSON
                    result TEXT, -- JSON
                    attempts INT DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    started_at TIMESTAMP,
                    completed_at TIMESTAMP
                )
            """)
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bg_tasks_status ON background_tasks(status, created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_bg_tasks_user ON background_tasks(user_id, status)")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS message_buffer (
                    user_id TEXT PRIMARY KEY,
                    events TEXT NOT NULL DEFAULT '[]', -- JSON
                    flush_at TIMESTAMP NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.commit()
