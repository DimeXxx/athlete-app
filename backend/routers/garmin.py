from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import os, json, base64
from database import get_db
from routers.auth import get_optional_user
from datetime import datetime, timedelta, date

router = APIRouter()

class GarminCredentials(BaseModel):
    email: str
    password: str

def get_uid(user):
    return user["id"] if user else 1

# Simple reversible encoding (not crypto, but hides plain text)
def encode_pwd(pwd: str) -> str:
    return base64.b64encode(pwd.encode()).decode()

def decode_pwd(encoded: str) -> str:
    return base64.b64decode(encoded.encode()).decode()

def save_garmin_creds(uid: int, email: str, password: str, db):
    encoded = encode_pwd(password)
    db.execute("""
        INSERT OR REPLACE INTO garmin_accounts (user_id, email, password_enc, updated_at)
        VALUES (?, ?, ?, ?)
    """, (uid, email, encoded, datetime.now().isoformat()))
    db.commit()

def load_garmin_creds(uid: int, db):
    row = db.execute(
        "SELECT email, password_enc FROM garmin_accounts WHERE user_id=?", (uid,)
    ).fetchone()
    if row:
        return {"email": row["email"], "password": decode_pwd(row["password_enc"])}
    return None

def ensure_garmin_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS garmin_accounts (
            user_id INTEGER PRIMARY KEY,
            email TEXT NOT NULL,
            password_enc TEXT NOT NULL,
            last_sync TEXT,
            updated_at TEXT
        )
    """)
    db.commit()

def get_garmin_client(email: str, password: str):
    try:
        from garminconnect import Garmin
        client = Garmin(email, password)
        client.login()
        return client
    except ImportError:
        raise HTTPException(500, "garminconnect not installed")
    except Exception as e:
        raise HTTPException(401, f"Garmin login failed: {str(e)}")

TYPE_MAP = {
    "swimming": "Swimming", "pool_swimming": "Swimming",
    "open_water_swimming": "Swimming", "lap_swimming": "Swimming",
    "running": "Running", "trail_running": "Running",
    "cycling": "Cycling", "indoor_cycling": "Cycling",
    "walking": "Walking", "hiking": "Hiking",
    "strength_training": "FunctionalStrengthTraining",
    "fitness_equipment": "FunctionalStrengthTraining",
}

def do_sync(uid: int, email: str, password: str, days: int, db):
    client = get_garmin_client(email, password)
    synced = skipped = 0
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    activities = client.get_activities_by_date(start_date.isoformat(), end_date.isoformat())
    for activity in activities:
        garmin_id = str(activity.get("activityId", ""))
        type_raw = activity.get("activityType", {}).get("typeKey", "other").lower()
        activity_type = TYPE_MAP.get(type_raw, "Other")
        start_time = activity.get("startTimeLocal", "")
        act_date = start_time[:10] if start_time else end_date.isoformat()
        duration_min = round(activity.get("duration", 0) / 60, 1)
        distance_km = round(activity.get("distance", 0) / 1000, 2)
        calories = activity.get("calories", 0)
        avg_hr = activity.get("averageHR")
        existing = db.execute(
            "SELECT id FROM workouts WHERE user_id=? AND garmin_id=?", (uid, garmin_id)
        ).fetchone()
        if existing:
            skipped += 1
            continue
        db.execute("""
            INSERT INTO workouts
            (user_id, date, type, duration_min, distance_km, calories, avg_hr, source, garmin_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, 'garmin', ?)
        """, (uid, act_date, activity_type, duration_min, distance_km, calories, avg_hr, garmin_id))
        db.commit()
        synced += 1
    # Update last sync time
    db.execute("UPDATE garmin_accounts SET last_sync=? WHERE user_id=?",
               (datetime.now().isoformat(), uid))
    db.commit()
    return synced, skipped, len(activities)

@router.get("/status")
def garmin_status(user=Depends(get_optional_user), db=Depends(get_db)):
    ensure_garmin_table(db)
    uid = get_uid(user)
    creds = load_garmin_creds(uid, db)
    if creds:
        row = db.execute("SELECT last_sync FROM garmin_accounts WHERE user_id=?", (uid,)).fetchone()
        return {"connected": True, "email": creds["email"], "last_sync": row["last_sync"] if row else None}
    return {"connected": False}

@router.post("/connect")
def garmin_connect(creds: GarminCredentials, user=Depends(get_optional_user), db=Depends(get_db)):
    ensure_garmin_table(db)
    uid = get_uid(user)
    client = get_garmin_client(creds.email, creds.password)
    try:
        name = client.get_full_name()
    except:
        name = creds.email
    save_garmin_creds(uid, creds.email, creds.password, db)
    return {"message": f"Подключён как {name}! Данные сохранены.", "connected": True}

@router.post("/sync")
def garmin_sync(creds: Optional[GarminCredentials] = None, days: int = 14,
                user=Depends(get_optional_user), db=Depends(get_db)):
    ensure_garmin_table(db)
    uid = get_uid(user)
    # Use saved creds if not provided
    if not creds or not creds.password:
        saved = load_garmin_creds(uid, db)
        if not saved:
            raise HTTPException(400, "Garmin не подключён. Введи email и пароль.")
        email, password = saved["email"], saved["password"]
    else:
        email, password = creds.email, creds.password
        save_garmin_creds(uid, email, password, db)
    synced, skipped, total = do_sync(uid, email, password, days, db)
    return {"message": "Синхронизация завершена!", "synced": synced, "skipped": skipped, "total": total}

@router.post("/auto-sync")
def auto_sync_all(db=Depends(get_db)):
    """Called by Railway Cron every 6 hours"""
    ensure_garmin_table(db)
    rows = db.execute("SELECT user_id, email, password_enc FROM garmin_accounts").fetchall()
    results = []
    for row in rows:
        try:
            pwd = decode_pwd(row["password_enc"])
            synced, skipped, total = do_sync(row["user_id"], row["email"], pwd, 2, db)
            results.append({"user_id": row["user_id"], "synced": synced})
        except Exception as e:
            results.append({"user_id": row["user_id"], "error": str(e)})
    return {"synced_users": len(results), "results": results}
