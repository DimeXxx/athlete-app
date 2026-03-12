from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import RedirectResponse
from database import get_db
from routers.auth import get_optional_user
from datetime import date, timedelta, datetime
import requests, os, json

router = APIRouter()

def get_uid(user):
    return user["id"] if user else 0

def ensure_strava_table(db):
    db.execute("""
        CREATE TABLE IF NOT EXISTS strava_accounts (
            user_id INTEGER PRIMARY KEY,
            strava_id INTEGER,
            athlete_name TEXT,
            access_token TEXT,
            refresh_token TEXT,
            token_expires_at INTEGER,
            last_sync TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.commit()

def get_valid_token(uid: int, db) -> str:
    """Get valid access token, refresh if expired"""
    row = db.execute(
        "SELECT access_token, refresh_token, token_expires_at FROM strava_accounts WHERE user_id=?",
        (uid,)
    ).fetchone()
    if not row:
        raise HTTPException(400, "Strava не подключён")

    # Check if token expired (with 5 min buffer)
    if row["token_expires_at"] and int(datetime.now().timestamp()) >= row["token_expires_at"] - 300:
        # Refresh token
        client_id = os.environ.get("STRAVA_CLIENT_ID")
        client_secret = os.environ.get("STRAVA_CLIENT_SECRET")
        resp = requests.post("https://www.strava.com/oauth/token", data={
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token",
            "refresh_token": row["refresh_token"]
        }, timeout=15)
        resp.raise_for_status()
        tokens = resp.json()
        db.execute("""
            UPDATE strava_accounts SET
                access_token=?, refresh_token=?, token_expires_at=?, updated_at=datetime('now')
            WHERE user_id=?
        """, (tokens["access_token"], tokens["refresh_token"], tokens["expires_at"], uid))
        db.commit()
        return tokens["access_token"]

    return row["access_token"]

# ─── OAuth flow ───────────────────────────────────────────────

@router.get("/connect")
def strava_connect(user=Depends(get_optional_user)):
    """Redirect user to Strava OAuth page"""
    uid = get_uid(user)
    client_id = os.environ.get("STRAVA_CLIENT_ID", "211013")
    base_url = os.environ.get("APP_URL", "https://smartfit.up.railway.app")
    redirect_uri = f"{base_url}/api/strava/callback"
    scope = "activity:read_all"
    url = (
        f"https://www.strava.com/oauth/authorize"
        f"?client_id={client_id}"
        f"&redirect_uri={redirect_uri}"
        f"&response_type=code"
        f"&approval_prompt=auto"
        f"&scope={scope}"
        f"&state={uid}"
    )
    return RedirectResponse(url)

@router.get("/callback")
def strava_callback(code: str = "", state: str = "0", error: str = "", db=Depends(get_db)):
    """Handle OAuth callback from Strava"""
    ensure_strava_table(db)

    if error:
        return RedirectResponse("/?strava=denied")

    client_id = os.environ.get("STRAVA_CLIENT_ID", "211013")
    client_secret = os.environ.get("STRAVA_CLIENT_SECRET", "")

    resp = requests.post("https://www.strava.com/oauth/token", data={
        "client_id": client_id,
        "client_secret": client_secret,
        "code": code,
        "grant_type": "authorization_code"
    }, timeout=15)
    resp.raise_for_status()
    data = resp.json()

    uid = int(state) if state.isdigit() else 0
    athlete = data.get("athlete", {})
    strava_id = athlete.get("id")
    name = f"{athlete.get('firstname','')} {athlete.get('lastname','')}".strip()

    db.execute("""
        INSERT INTO strava_accounts (user_id, strava_id, athlete_name, access_token, refresh_token, token_expires_at)
        VALUES (?,?,?,?,?,?)
        ON CONFLICT(user_id) DO UPDATE SET
            strava_id=excluded.strava_id,
            athlete_name=excluded.athlete_name,
            access_token=excluded.access_token,
            refresh_token=excluded.refresh_token,
            token_expires_at=excluded.token_expires_at,
            updated_at=datetime('now')
    """, (uid, strava_id, name, data["access_token"], data["refresh_token"], data["expires_at"]))
    db.commit()

    return RedirectResponse("/?strava=connected")

# ─── Status & Sync ────────────────────────────────────────────

@router.get("/status")
def strava_status(user=Depends(get_optional_user), db=Depends(get_db)):
    ensure_strava_table(db)
    uid = get_uid(user)
    row = db.execute(
        "SELECT athlete_name, last_sync, strava_id FROM strava_accounts WHERE user_id=?", (uid,)
    ).fetchone()
    if row:
        return {"connected": True, "athlete": row["athlete_name"], "last_sync": row["last_sync"]}
    return {"connected": False}

@router.post("/sync")
def strava_sync(days: int = 30, user=Depends(get_optional_user), db=Depends(get_db)):
    ensure_strava_table(db)
    uid = get_uid(user)
    token = get_valid_token(uid, db)

    TYPE_MAP = {
        "Run": "Running", "TrailRun": "Running",
        "Swim": "Swimming",
        "Ride": "Cycling", "VirtualRide": "Cycling",
        "Walk": "Walking", "Hike": "Hiking",
        "WeightTraining": "FunctionalStrengthTraining",
        "Workout": "FunctionalStrengthTraining",
        "RockClimbing": "Other",
    }

    after_ts = int((datetime.now() - timedelta(days=days)).timestamp())
    page = 1
    synced = skipped = 0

    while True:
        resp = requests.get(
            "https://www.strava.com/api/v3/athlete/activities",
            headers={"Authorization": f"Bearer {token}"},
            params={"after": after_ts, "per_page": 100, "page": page},
            timeout=20
        )
        resp.raise_for_status()
        activities = resp.json()
        if not activities:
            break

        for a in activities:
            strava_id = f"strava_{a['id']}"
            existing = db.execute(
                "SELECT id FROM workouts WHERE user_id=? AND garmin_id=?", (uid, strava_id)
            ).fetchone()
            if existing:
                skipped += 1
                continue

            act_type = TYPE_MAP.get(a.get("type", ""), "Other")
            act_date = a["start_date_local"][:10]
            distance_km = round(a.get("distance", 0) / 1000, 2)
            duration_min = round(a.get("moving_time", 0) / 60, 1)
            calories = a.get("kilojoules", 0) or 0
            avg_hr = a.get("average_heartrate")
            max_hr = a.get("max_heartrate")
            elevation = a.get("total_elevation_gain")
            avg_cadence = None
            if a.get("average_cadence"):
                avg_cadence = round(a["average_cadence"] * 2)  # Strava = steps/min per leg

            # Pace for running
            avg_pace = None
            if distance_km > 0 and duration_min > 0 and act_type == "Running":
                pace_secs = (duration_min * 60) / distance_km
                avg_pace = f"{int(pace_secs//60)}:{int(pace_secs%60):02d}"

            db.execute("""
                INSERT INTO workouts
                (user_id, date, type, duration_min, distance_km, calories, avg_hr, max_hr,
                 avg_cadence, elevation_gain, avg_pace, notes, source, garmin_id)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (uid, act_date, act_type, duration_min, distance_km, int(calories),
                  avg_hr, max_hr, avg_cadence, elevation, avg_pace,
                  a.get("name", ""), "strava", strava_id))
            synced += 1

        db.commit()
        if len(activities) < 100:
            break
        page += 1

    db.execute("UPDATE strava_accounts SET last_sync=? WHERE user_id=?",
               (datetime.now().isoformat(), uid))
    db.commit()
    return {"synced": synced, "skipped": skipped, "message": f"Загружено {synced} тренировок из Strava"}

@router.post("/disconnect")
def strava_disconnect(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    db.execute("DELETE FROM strava_accounts WHERE user_id=?", (uid,))
    db.commit()
    return {"message": "Strava отключена"}
