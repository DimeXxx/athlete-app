import sqlite3
import os

DB_PATH = os.environ.get("DB_PATH", "athlete.db")

def get_db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    c.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT UNIQUE NOT NULL,
            name TEXT,
            password_hash TEXT NOT NULL,
            token TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT NOT NULL,
            type TEXT NOT NULL,
            duration_min REAL,
            distance_km REAL,
            calories INTEGER,
            avg_hr INTEGER,
            max_hr INTEGER,
            avg_cadence INTEGER,
            elevation_gain REAL,
            avg_pace TEXT,
            notes TEXT,
            ai_analysis TEXT,
            source TEXT DEFAULT 'manual',
            garmin_id TEXT,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, garmin_id)
        )
    """)

    # Migrate: add new columns if not exist
    for col, typedef in [
        ("max_hr", "INTEGER"), ("avg_cadence", "INTEGER"),
        ("elevation_gain", "REAL"), ("avg_pace", "TEXT"), ("ai_analysis", "TEXT")
    ]:
        try:
            c.execute(f"ALTER TABLE workouts ADD COLUMN {col} {typedef}")
            conn.commit()
        except Exception:
            pass

    c.execute("""
        CREATE TABLE IF NOT EXISTS nutrition (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT NOT NULL,
            meal_name TEXT NOT NULL,
            calories INTEGER DEFAULT 0,
            protein_g REAL DEFAULT 0,
            carbs_g REAL DEFAULT 0,
            fat_g REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS health_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            date TEXT NOT NULL,
            steps INTEGER,
            resting_hr INTEGER,
            sleep_hours REAL,
            weight_kg REAL,
            vo2max REAL,
            hrv INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, date)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(user_id, key)
        )
    """)

    c.execute("""
        CREATE TABLE IF NOT EXISTS user_plans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER UNIQUE,
            plan_json TEXT NOT NULL,
            race_date TEXT,
            race_name TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    conn.commit()
    conn.close()
    print("✅ Database initialized")
