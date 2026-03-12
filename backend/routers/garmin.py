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
    return user["id"] if user else 0  # 0 = guest, no real data

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

def sync_health_metrics(uid: int, client, days: int, db):
    """Sync sleep, HRV, weight, resting HR from Garmin daily summaries"""
    end_date = date.today()
    start_date = end_date - timedelta(days=min(days, 30))  # health metrics: last 30 days
    synced = 0
    check_date = start_date
    while check_date <= end_date:
        date_str = check_date.isoformat()
        try:
            summary = client.get_user_summary(date_str)
            if not summary:
                check_date += timedelta(days=1)
                continue

            sleep_h = None
            if summary.get("sleepingSeconds"):
                sleep_h = round(summary["sleepingSeconds"] / 3600, 1)
            elif summary.get("sleepTimeSeconds"):
                sleep_h = round(summary["sleepTimeSeconds"] / 3600, 1)

            resting_hr = summary.get("restingHeartRate")
            steps      = summary.get("totalSteps")
            weight_kg  = None  # will try body composition below

            # HRV — from HRV summary if available
            hrv = None
            try:
                hrv_data = client.get_hrv_data(date_str)
                if hrv_data and hrv_data.get("hrvSummary"):
                    hrv = hrv_data["hrvSummary"].get("lastNight") or hrv_data["hrvSummary"].get("weeklyAvg")
            except Exception:
                pass

            # Weight from body composition
            try:
                bc = client.get_body_composition(date_str, date_str)
                if bc and bc.get("dateWeightList"):
                    w = bc["dateWeightList"][0].get("weight")
                    if w: weight_kg = round(w / 1000, 1)
            except Exception:
                pass

            # Only update if we have at least something useful
            if any(v is not None for v in [sleep_h, resting_hr, hrv, weight_kg, steps]):
                db.execute("""
                    INSERT INTO health_metrics (user_id, date, steps, resting_hr, sleep_hours, weight_kg, hrv)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(user_id, date) DO UPDATE SET
                      steps      = COALESCE(excluded.steps, steps),
                      resting_hr = COALESCE(excluded.resting_hr, resting_hr),
                      sleep_hours= COALESCE(excluded.sleep_hours, sleep_hours),
                      weight_kg  = COALESCE(excluded.weight_kg, weight_kg),
                      hrv        = COALESCE(excluded.hrv, hrv)
                """, (uid, date_str, steps, resting_hr, sleep_h, weight_kg, hrv))
                db.commit()
                synced += 1
        except Exception:
            pass
        check_date += timedelta(days=1)
    return synced


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

    # Also sync health metrics (sleep, HRV, weight, resting HR)
    try:
        sync_health_metrics(uid, client, days, db)
    except Exception:
        pass

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
def garmin_sync(creds: Optional[GarminCredentials] = None, days: int = 0,
                user=Depends(get_optional_user), db=Depends(get_db)):
    ensure_garmin_table(db)
    uid = get_uid(user)
    if not creds or not creds.password:
        saved = load_garmin_creds(uid, db)
        if not saved:
            raise HTTPException(400, "Garmin не подключён. Введи email и пароль.")
        email, password = saved["email"], saved["password"]
    else:
        email, password = creds.email, creds.password
        save_garmin_creds(uid, email, password, db)

    # First sync ever = load 2 years; repeat sync = load 30 days
    if days == 0:
        existing = db.execute(
            "SELECT COUNT(*) FROM workouts WHERE user_id=? AND source='garmin'", (uid,)
        ).fetchone()[0]
        # Check oldest record - if less than 1 year of data, load 2 years
        oldest = db.execute(
            "SELECT MIN(date) FROM workouts WHERE user_id=? AND source='garmin'", (uid,)
        ).fetchone()[0]
        if not oldest:
            days = 730  # No data - load 2 years
        else:
            from datetime import date as _date
            oldest_date = _date.fromisoformat(oldest)
            days_of_data = (_date.today() - oldest_date).days
            days = 730 if days_of_data < 365 else 30  # Less than 1yr? load 2yrs


    synced, skipped, total = do_sync(uid, email, password, days, db)
    # Sync daily steps — try multiple Garmin API methods
    steps_synced = 0
    try:
        steps_data = None
        # Try different method names (garminconnect API varies by version)
        for method_name in ['get_steps_data', 'get_daily_steps', 'get_user_summary_chart']:
            try:
                method = getattr(client, method_name, None)
                if method:
                    steps_data = method(start_date.isoformat(), end_date.isoformat())
                    if steps_data:
                        break
            except Exception:
                continue

        # Also try extracting steps from daily summary
        if not steps_data:
            try:
                from datetime import timedelta as _td
                cur = start_date
                steps_data = []
                while cur <= end_date:
                    try:
                        summary = client.get_user_summary(cur.isoformat())
                        s = summary.get("totalSteps") or summary.get("steps")
                        if s:
                            steps_data.append({"calendarDate": cur.isoformat(), "totalSteps": s})
                    except Exception:
                        pass
                    cur += _td(days=1)
            except Exception:
                pass

        for day in (steps_data or []):
            day_date = str(day.get("calendarDate", ""))[:10]
            steps = (day.get("totalSteps") or day.get("steps") or
                     day.get("totalStep") or day.get("stepGoal"))
            if day_date and steps and int(steps) > 0:
                db.execute(
                    "INSERT INTO health_metrics (user_id, date, steps) VALUES (?,?,?) "
                    "ON CONFLICT(user_id, date) DO UPDATE SET steps=COALESCE(excluded.steps,steps)",
                    (uid, day_date, int(steps))
                )
                steps_synced += 1
        if steps_synced:
            db.commit()
    except Exception:
        pass

    return {"message": "Синхронизация завершена!", "synced": synced, "skipped": skipped,
            "total": total, "steps_synced": steps_synced}

@router.post("/full-sync")
def full_sync(creds: Optional[GarminCredentials] = None,
              user=Depends(get_optional_user), db=Depends(get_db)):
    """Force full 2-year history reload"""
    ensure_garmin_table(db)
    uid = get_uid(user)
    if not creds or not creds.password:
        saved = load_garmin_creds(uid, db)
        if not saved:
            raise HTTPException(400, "Garmin не подключён")
        email, password = saved["email"], saved["password"]
    else:
        email, password = creds.email, creds.password
        save_garmin_creds(uid, email, password, db)
    synced, skipped, total = do_sync(uid, email, password, 730, db)
    return {"message": "Полная история загружена!", "synced": synced,
            "skipped": skipped, "total": total, "days_loaded": 730}

@router.post("/sync-steps")
def sync_steps_only(creds: Optional[GarminCredentials] = None, days: int = 90,
                    user=Depends(get_optional_user), db=Depends(get_db)):
    ensure_garmin_table(db)
    uid = get_uid(user)
    if not creds or not creds.password:
        saved = load_garmin_creds(uid, db)
        if not saved:
            raise HTTPException(400, "Garmin не подключён")
        email, password = saved["email"], saved["password"]
    else:
        email, password = creds.email, creds.password

    client = get_garmin_client(email, password)
    end_date = date.today()
    start_date = end_date - timedelta(days=days)
    steps_synced = 0
    errors = []

    try:
        from datetime import timedelta as _td
        cur = start_date
        while cur <= end_date:
            try:
                summary = client.get_user_summary(cur.isoformat())
                steps = (summary.get("totalSteps") or summary.get("steps", 0))
                rhr = summary.get("restingHeartRate")
                if steps and int(steps) > 0:
                    db.execute(
                        "INSERT INTO health_metrics (user_id, date, steps, resting_hr) VALUES (?,?,?,?) "
                        "ON CONFLICT(user_id, date) DO UPDATE SET "
                        "steps=COALESCE(excluded.steps,steps),"
                        "resting_hr=COALESCE(excluded.resting_hr,resting_hr)",
                        (uid, cur.isoformat(), int(steps), rhr)
                    )
                    steps_synced += 1
            except Exception as e:
                errors.append(str(cur))
            cur += _td(days=1)
        db.commit()
    except Exception as e:
        raise HTTPException(500, str(e))

    return {"synced_days": steps_synced, "period_days": days, "errors": len(errors)}

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
