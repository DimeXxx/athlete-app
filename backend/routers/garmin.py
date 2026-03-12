from fastapi import APIRouter, HTTPException, BackgroundTasks
from pydantic import BaseModel
from typing import Optional
import os
import json
from database import get_db
from fastapi import Depends
from datetime import datetime, timedelta, date

router = APIRouter()

class GarminCredentials(BaseModel):
    email: str
    password: str

class GarminStatus(BaseModel):
    connected: bool
    last_sync: Optional[str] = None
    email: Optional[str] = None

def get_garmin_client(email: str, password: str):
    """Try to connect to Garmin Connect"""
    try:
        from garminconnect import Garmin
        client = Garmin(email, password)
        client.login()
        return client
    except ImportError:
        raise HTTPException(500, "garminconnect package not installed. Run: pip install garminconnect")
    except Exception as e:
        raise HTTPException(401, f"Garmin login failed: {str(e)}")

GARMIN_CREDS_FILE = "garmin_session.json"

def save_credentials(email: str):
    with open(GARMIN_CREDS_FILE, "w") as f:
        json.dump({"email": email, "last_sync": datetime.now().isoformat()}, f)

def load_credentials():
    if os.path.exists(GARMIN_CREDS_FILE):
        with open(GARMIN_CREDS_FILE) as f:
            return json.load(f)
    return None

@router.get("/status")
def garmin_status():
    creds = load_credentials()
    if creds:
        return {"connected": True, "email": creds.get("email"), "last_sync": creds.get("last_sync")}
    return {"connected": False}

@router.post("/connect")
def garmin_connect(creds: GarminCredentials, db=Depends(get_db)):
    """Test Garmin credentials and save session"""
    client = get_garmin_client(creds.email, creds.password)
    # Test the connection
    profile = client.get_full_name()
    save_credentials(creds.email)
    return {"message": f"Connected as {profile}!", "connected": True}

@router.post("/sync")
def garmin_sync(creds: GarminCredentials, days: int = 7, db=Depends(get_db)):
    """Sync last N days of workouts from Garmin"""
    client = get_garmin_client(creds.email, creds.password)

    synced = 0
    skipped = 0
    errors = []

    # Map Garmin activity types to our types
    TYPE_MAP = {
        "swimming": "Swimming",
        "pool_swimming": "Swimming",
        "open_water_swimming": "Swimming",
        "running": "Running",
        "trail_running": "Running",
        "cycling": "Cycling",
        "indoor_cycling": "Cycling",
        "walking": "Walking",
        "hiking": "Hiking",
        "strength_training": "FunctionalStrengthTraining",
        "fitness_equipment": "FunctionalStrengthTraining",
        "cardio_training": "MixedCardio",
        "tennis": "Tennis",
        "squash": "Squash",
        "rowing": "Rowing",
    }

    try:
        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        activities = client.get_activities_by_date(
            start_date.isoformat(),
            end_date.isoformat()
        )

        for activity in activities:
            garmin_id = str(activity.get("activityId", ""))
            activity_type_raw = activity.get("activityType", {}).get("typeKey", "other").lower()
            activity_type = TYPE_MAP.get(activity_type_raw, "Other")

            start_time = activity.get("startTimeLocal", "")
            act_date = start_time[:10] if start_time else end_date.isoformat()
            duration_min = round(activity.get("duration", 0) / 60, 1)
            distance_km = round(activity.get("distance", 0) / 1000, 2)
            calories = activity.get("calories", 0)
            avg_hr = activity.get("averageHR")

            try:
                db.execute("""
                    INSERT OR IGNORE INTO workouts
                    (date, type, duration_min, distance_km, calories, avg_hr, source, garmin_id)
                    VALUES (?, ?, ?, ?, ?, ?, 'garmin', ?)
                """, (act_date, activity_type, duration_min, distance_km, calories, avg_hr, garmin_id))
                db.commit()
                synced += 1
            except Exception as e:
                skipped += 1

        save_credentials(creds.email)
        return {
            "message": f"Sync complete!",
            "synced": synced,
            "skipped": skipped,
            "total_activities": len(activities)
        }

    except Exception as e:
        raise HTTPException(500, f"Sync error: {str(e)}")

@router.get("/activities/today")
def get_today_garmin(creds_email: str, creds_password: str, db=Depends(get_db)):
    """Quick sync just today"""
    client = get_garmin_client(creds_email, creds_password)
    today = date.today().isoformat()
    activities = client.get_activities_by_date(today, today)
    return {"activities": activities, "count": len(activities)}
