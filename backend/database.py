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

    # Workouts table
    c.execute("""
        CREATE TABLE IF NOT EXISTS workouts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            type TEXT NOT NULL,
            duration_min REAL,
            distance_km REAL,
            calories INTEGER,
            avg_hr INTEGER,
            notes TEXT,
            source TEXT DEFAULT 'manual',
            garmin_id TEXT UNIQUE,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Nutrition table (daily log)
    c.execute("""
        CREATE TABLE IF NOT EXISTS nutrition (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            meal_name TEXT NOT NULL,
            calories INTEGER DEFAULT 0,
            protein_g REAL DEFAULT 0,
            carbs_g REAL DEFAULT 0,
            fat_g REAL DEFAULT 0,
            source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Health metrics table
    c.execute("""
        CREATE TABLE IF NOT EXISTS health_metrics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            date TEXT NOT NULL,
            steps INTEGER,
            resting_hr INTEGER,
            sleep_hours REAL,
            sleep_deep_min INTEGER,
            sleep_rem_min INTEGER,
            weight_kg REAL,
            vo2max REAL,
            hrv INTEGER,
            source TEXT DEFAULT 'manual',
            created_at TEXT DEFAULT (datetime('now')),
            UNIQUE(date)
        )
    """)

    # Goals table
    c.execute("""
        CREATE TABLE IF NOT EXISTS goals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)

    # Seed default goals
    defaults = [
        ("calories_target", "2300"),
        ("protein_target", "150"),
        ("carbs_target", "230"),
        ("fat_target", "75"),
        ("steps_target", "10000"),
        ("sleep_target", "7.5"),
        ("weight_kg", "67"),
        ("race_date", "2026-04-26"),
        ("race_name", "Полумарафон 21 км"),
    ]
    for key, value in defaults:
        c.execute("INSERT OR IGNORE INTO goals (key, value) VALUES (?, ?)", (key, value))

    conn.commit()
    conn.close()
    print("✅ Database initialized")
