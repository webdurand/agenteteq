import sqlite3
import os

DB_PATH = "users.db"

def _get_connection():
    conn = sqlite3.connect(DB_PATH)
    return conn

def init_db():
    conn = _get_connection()
    c = conn.cursor()
    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            phone_number TEXT PRIMARY KEY,
            name TEXT,
            onboarding_step TEXT DEFAULT 'pending'
        )
    """)
    conn.commit()
    conn.close()

def get_user(phone_number: str) -> dict:
    init_db()
    conn = _get_connection()
    c = conn.cursor()
    c.execute("SELECT phone_number, name, onboarding_step FROM users WHERE phone_number = ?", (phone_number,))
    row = c.fetchone()
    conn.close()
    if row:
        return {"phone_number": row[0], "name": row[1], "onboarding_step": row[2]}
    return None

def create_user(phone_number: str):
    init_db()
    conn = _get_connection()
    c = conn.cursor()
    c.execute("INSERT INTO users (phone_number, onboarding_step) VALUES (?, 'asking_name')", (phone_number,))
    conn.commit()
    conn.close()

def update_user_name(phone_number: str, name: str):
    init_db()
    conn = _get_connection()
    c = conn.cursor()
    c.execute("UPDATE users SET name = ?, onboarding_step = 'completed' WHERE phone_number = ?", (name, phone_number))
    conn.commit()
    conn.close()
