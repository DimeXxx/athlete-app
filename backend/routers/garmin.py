from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional
import os, json
from database import get_db
from routers.auth import get_optional_user
from datetime import datetime, timedelta, date

router = APIRouter()

class GarminCredentials(BaseModel):
    email: str
    password: str

GARMIN_CREDS_FILE = "garmin_session.json"

def save_credentials(email: str):
    with open(GARMIN_CREDS_FILE, "w") as f:
        json.dump({"email": email, "last_sync": datetime.now().isoformat()}, f)

def load_credentials():
    if os.path.exists(GARMIN_CREDS_FILE):
        with open(GARMIN_CREDS_FILE) as f:
            return json.load(f)
    return None

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
    "tennis": "Tennis", "squash": "Squash",
}

@router.get("/status")
def garmin_status():
    creds = load_credentials()
    if creds:
        return {"connected": True, "email": creds.get("email"), "last_sync": creds.get("last_sync")}
    return {"connected": False}

@router.post("/connect")
def garmin_connect(creds: GarminCredentials):
    client = get_garmin_client(creds.email, creds.password)
    try:
        name = client.get_full_name()
    except:
        name = creds.email
    save_credentials(creds.email)
    return {"message": f"Подключён как {name}!", "connected": True}

@router.post("/sync")
def garmin_sync(creds: GarminCredentials, days: int = 14,
                user=Depends(get_optional_user), db=Depends(get_db)):
    # Get user_id — if logged in use their id, else use 1
    uid = user["id"] if user else 1

    client = get_garmin_client(creds.email, creds.password)
    synced = 0
    skipped = 0

    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)
        activities = client.get_activities_by_date(
            start_date.isoformat(), end_date.isoformat()
        )

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

            # Check if already exists for this user
            existing = db.execute(
                "SELECT id FROM workouts WHERE user_id=? AND garmin_id=?",
                (uid, garmin_id)
            ).fetchone()

            if existing:
                skipped += 1
                continue

            db.execute("""
                INSERT INTO workouts
                (user_id, date, type, duration_min, distance_km, calories, avg_hr, source, garmin_id)
                VALUES (?, ?, ?, ?, ?, ?, ?, 'garmin', ?)
            """, (uid, act_date, activity_type, duration_min,
                  distance_km, calories, avg_hr, garmin_id))
            db.commit()
            synced += 1

        save_credentials(creds.email)
        return {
            "message": "Синхронизация завершена!",
            "synced": synced,
            "skipped": skipped,
            "total": len(activities),
            "user_id": uid
        }

    except Exception as e:
        raise HTTPException(500, f"Sync error: {str(e)}")
