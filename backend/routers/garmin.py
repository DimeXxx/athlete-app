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
    mfa_code: str = ""  # optional MFA code


def get_garmin_token_dir(uid: int) -> str:
    """Per-user token directory"""
    import tempfile
    base = os.environ.get("GARMIN_TOKEN_DIR", "/tmp/garmin_tokens")
    path = os.path.join(base, str(uid))
    os.makedirs(path, exist_ok=True)
    return path


def get_garmin_client_with_tokens(uid: int, email: str, password: str, mfa_code: str = ""):
    """Login with garth token support — handles 2FA properly"""
    try:
        from garminconnect import Garmin
        import garth
    except ImportError:
        raise HTTPException(500, "garminconnect not installed")

    token_dir = get_garmin_token_dir(uid)

    # Try resuming from saved tokens first
    try:
        client = Garmin(email=email, password=password)
        client.garth.load(token_dir)
        client.garth.client.auth_token.refresh()
        return client, False  # False = no MFA needed
    except Exception:
        pass  # tokens missing or expired, do fresh login

    # Fresh login — may need MFA
    try:
        if mfa_code:
            # Login with MFA code provided
            client = Garmin(email=email, password=password, prompt_mfa=lambda: mfa_code)
        else:
            # Attempt login without MFA (works if 2FA not enabled)
            client = Garmin(email=email, password=password)

        client.login()
        # Save tokens for next time
        client.garth.dump(token_dir)
        return client, False

    except Exception as e:
        err = str(e).lower()
        if "mfa" in err or "2fa" in err or "factor" in err or "verification" in err or "code" in err:
            raise HTTPException(403, "MFA_REQUIRED")
        if "invalid" in err or "unauthorized" in err or "401" in err or "password" in err or "incorrect" in err:
            raise HTTPException(401, "Неверный email или пароль Garmin.")
        raise HTTPException(401, f"Ошибка Garmin: {str(e)}")

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

def get_garmin_client(email: str, password: str, uid: int = 0, token_store: dict = None):
    """Legacy wrapper — tries token auth first, falls back to password"""
    try:
        client, _ = get_garmin_client_with_tokens(uid, email, password)
        return client
    except HTTPException as e:
        if "MFA_REQUIRED" in str(e.detail):
            raise HTTPException(401, "Garmin требует код 2FA. Переподключи Garmin в разделе Прогресс → Garmin.")
        raise

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
    start_date = end_date - timedelta(days=min(days, 30))
    synced = 0
    check_date = start_date
    while check_date <= end_date:
        date_str = check_date.isoformat()
        try:
            # --- Summary: steps, resting HR ---
            resting_hr = None
            steps = None
            try:
                summary = client.get_user_summary(date_str)
                if summary:
                    resting_hr = summary.get("restingHeartRate")
                    steps = summary.get("totalSteps")
            except Exception:
                pass

            # --- Sleep: from get_sleep_data ---
            # sleepTimeSeconds = total sleep (correct)
            # sleepingSeconds  = only deep/light sleep (wrong, ~4.7h)
            sleep_h = None
            try:
                sleep_data = client.get_sleep_data(date_str)
                dto = sleep_data.get("dailySleepDTO", {}) if sleep_data else {}
                secs = dto.get("sleepTimeSeconds")  # total sleep time — correct field
                if not secs:
                    # fallback: sum from summary sleepingSeconds only if no other option
                    secs = dto.get("sleepingSeconds")
                if secs and secs > 0:
                    sleep_h = round(secs / 3600, 1)
            except Exception:
                pass

            # --- HRV: lastNightAvg preferred, fallback weeklyAvg ---
            hrv = None
            try:
                hrv_data = client.get_hrv_data(date_str)
                if hrv_data and hrv_data.get("hrvSummary"):
                    s = hrv_data["hrvSummary"]
                    hrv = s.get("lastNightAvg") or s.get("weeklyAvg") or s.get("lastNight5MinHigh")
            except Exception:
                pass

            # --- Weight from body composition ---
            weight_kg = None
            try:
                bc = client.get_body_composition(date_str, date_str)
                if bc and bc.get("dateWeightList"):
                    w = bc["dateWeightList"][0].get("weight")
                    if w: weight_kg = round(w / 1000, 1)
            except Exception:
                pass

            if any(v is not None for v in [sleep_h, resting_hr, hrv, weight_kg, steps]):
                db.execute("""
                    INSERT INTO health_metrics (user_id, date, steps, resting_hr, sleep_hours, weight_kg, hrv)
                    VALUES (?,?,?,?,?,?,?)
                    ON CONFLICT(user_id, date) DO UPDATE SET
                      steps       = COALESCE(excluded.steps, steps),
                      resting_hr  = COALESCE(excluded.resting_hr, resting_hr),
                      sleep_hours = COALESCE(excluded.sleep_hours, sleep_hours),
                      weight_kg   = COALESCE(excluded.weight_kg, weight_kg),
                      hrv         = COALESCE(excluded.hrv, hrv)
                """, (uid, date_str, steps, resting_hr, sleep_h, weight_kg, hrv))
                db.commit()
                synced += 1
        except Exception:
            pass
        check_date += timedelta(days=1)
    return synced


def do_sync(uid: int, email: str, password: str, days: int, db):
    client = get_garmin_client(email, password, uid=uid)
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
        max_hr = activity.get("maxHR")
        avg_cadence = activity.get("averageRunningCadenceInStepsPerMinute") or                       activity.get("averageBikingCadenceInRevPerMinute") or                       activity.get("averageSwimmingCadenceInStrokesPerMinute")
        if avg_cadence: avg_cadence = round(avg_cadence)
        elevation_gain = activity.get("elevationGain")
        if elevation_gain: elevation_gain = round(elevation_gain, 1)
        # Compute avg pace (min/km) for running
        avg_pace = None
        if distance_km and distance_km > 0 and duration_min and activity_type in ("Running", "RunLong"):
            pace_secs = (duration_min * 60) / distance_km
            avg_pace = f"{int(pace_secs//60)}:{int(pace_secs%60):02d}"
        existing = db.execute(
            "SELECT id FROM workouts WHERE user_id=? AND garmin_id=?", (uid, garmin_id)
        ).fetchone()
        if existing:
            skipped += 1
            continue
        db.execute("""
            INSERT INTO workouts
            (user_id, date, type, duration_min, distance_km, calories, avg_hr, max_hr,
             avg_cadence, elevation_gain, avg_pace, source, garmin_id)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'garmin', ?)
        """, (uid, act_date, activity_type, duration_min, distance_km, calories,
              avg_hr, max_hr, avg_cadence, elevation_gain, avg_pace, garmin_id))
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

@router.get("/debug-health")
def debug_health(user=Depends(get_optional_user), db=Depends(get_db)):
    """Debug: show raw Garmin health data for today and yesterday"""
    ensure_garmin_table(db)
    uid = get_uid(user)
    saved = load_garmin_creds(uid, db)
    if not saved:
        raise HTTPException(400, "Garmin не подключён")
    client = get_garmin_client(saved["email"], saved["password"], uid=uid)
    results = {}
    for d in [date.today().isoformat(), (date.today()-timedelta(days=1)).isoformat()]:
        try:
            summary = client.get_user_summary(d)
            results[f"summary_{d}"] = {
                "sleepingSeconds": summary.get("sleepingSeconds"),
                "sleepTimeSeconds": summary.get("sleepTimeSeconds"),
                "restingHeartRate": summary.get("restingHeartRate"),
                "totalSteps": summary.get("totalSteps"),
                "keys_available": list(summary.keys())[:20] if summary else []
            }
        except Exception as e:
            results[f"summary_{d}"] = {"error": str(e)}
        try:
            hrv = client.get_hrv_data(d)
            results[f"hrv_{d}"] = hrv.get("hrvSummary") if hrv else None
        except Exception as e:
            results[f"hrv_{d}"] = {"error": str(e)}
        try:
            sleep = client.get_sleep_data(d)
            results[f"sleep_{d}"] = {
                "dailySleepDTO": sleep.get("dailySleepDTO", {}) if sleep else None
            }
        except Exception as e:
            results[f"sleep_{d}"] = {"error": str(e)}
    return results

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
    try:
        client, _ = get_garmin_client_with_tokens(uid, creds.email, creds.password, creds.mfa_code)
    except HTTPException as e:
        if e.detail == "MFA_REQUIRED":
            # Tell frontend to ask for MFA code
            from fastapi.responses import JSONResponse
            return JSONResponse(status_code=200, content={"mfa_required": True, "message": "Введи код из email/SMS"})
        raise
    try:
        name = client.get_full_name()
    except:
        name = creds.email
    save_garmin_creds(uid, creds.email, creds.password, db)
    return {"message": f"Подключён как {name}! Данные сохранены.", "connected": True, "mfa_required": False}

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
        oldest = db.execute(
            "SELECT MIN(date) FROM workouts WHERE user_id=? AND source='garmin'", (uid,)
        ).fetchone()[0]
        if not oldest:
            days = 730
        else:
            from datetime import date as _date
            days_of_data = (_date.today() - _date.fromisoformat(oldest)).days
            days = 730 if days_of_data < 365 else 30

    synced, skipped, total = do_sync(uid, email, password, days, db)

    # Sync steps via client
    steps_synced = 0
    try:
        client = get_garmin_client(email, password, uid=uid)
        from datetime import date as _date2, timedelta as _td
        end_date = _date2.today()
        start_date = end_date - _td(days=min(days, 90))

        steps_data = []
        cur = start_date
        while cur <= end_date:
            try:
                summary = client.get_user_summary(cur.isoformat())
                s = summary.get("totalSteps") or summary.get("steps")
                if s and int(s) > 0:
                    steps_data.append({"calendarDate": cur.isoformat(), "totalSteps": int(s)})
            except Exception:
                pass
            cur += _td(days=1)

        for day in steps_data:
            day_date = str(day.get("calendarDate", ""))[:10]
            steps = day.get("totalSteps", 0)
            if day_date and steps:
                db.execute(
                    "INSERT INTO health_metrics (user_id, date, steps) VALUES (?,?,?) "
                    "ON CONFLICT(user_id, date) DO UPDATE SET steps=COALESCE(excluded.steps,steps)",
                    (uid, day_date, steps)
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

    client = get_garmin_client(email, password, uid=uid)

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
