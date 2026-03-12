from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, EmailStr
from typing import Optional
import sqlite3, hashlib, secrets, os, json
from database import get_db
from datetime import datetime

router = APIRouter()

# Simple token store (in DB)
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token() -> str:
    return secrets.token_urlsafe(32)

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""

class LoginRequest(BaseModel):
    email: str
    password: str

def get_current_user(authorization: Optional[str] = Header(None), db=Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.replace("Bearer ", "")
    row = db.execute(
        "SELECT * FROM users WHERE token = ?", (token,)
    ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid token")
    return dict(row)

def get_optional_user(authorization: Optional[str] = Header(None), db=Depends(get_db)):
    """Returns user or None — for endpoints that work both ways"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.replace("Bearer ", "")
    row = db.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
    return dict(row) if row else None

@router.post("/register")
def register(req: RegisterRequest, db=Depends(get_db)):
    existing = db.execute("SELECT id FROM users WHERE email = ?", (req.email,)).fetchone()
    if existing:
        raise HTTPException(400, "Email already registered")
    
    token = generate_token()
    db.execute("""
        INSERT INTO users (email, name, password_hash, token, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (req.email, req.name or req.email.split("@")[0],
          hash_password(req.password), token, datetime.now().isoformat()))
    db.commit()
    
    user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    
    # Seed default goals for new user
    defaults = [
        ("calories_target", "2300"), ("protein_target", "150"),
        ("carbs_target", "230"), ("fat_target", "75"),
        ("steps_target", "10000"), ("sleep_target", "7.5"),
        ("weight_kg", "70"), ("race_date", "2026-04-26"),
        ("race_name", "Полумарафон 21 км"),
    ]
    for key, value in defaults:
        db.execute(
            "INSERT OR IGNORE INTO goals (user_id, key, value) VALUES (?, ?, ?)",
            (user_id, key, value)
        )
    db.commit()
    
    return {
        "token": token,
        "user": {"id": user_id, "email": req.email, "name": req.name or req.email.split("@")[0]}
    }

@router.post("/login")
def login(req: LoginRequest, db=Depends(get_db)):
    row = db.execute(
        "SELECT * FROM users WHERE email = ? AND password_hash = ?",
        (req.email, hash_password(req.password))
    ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid email or password")
    
    # Refresh token on login
    token = generate_token()
    db.execute("UPDATE users SET token = ? WHERE id = ?", (token, row["id"]))
    db.commit()
    
    return {
        "token": token,
        "user": {"id": row["id"], "email": row["email"], "name": row["name"]}
    }

@router.get("/me")
def me(user=Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"]}

@router.post("/logout")  
def logout(user=Depends(get_current_user), db=Depends(get_db)):
    db.execute("UPDATE users SET token = NULL WHERE id = ?", (user["id"],))
    db.commit()
    return {"message": "Logged out"}


@router.get("/profile")
def get_profile(user=Depends(get_optional_user), db=Depends(get_db)):
    if not user:
        raise HTTPException(401, "Not authenticated")
    uid = user["id"]
    goals = {r["key"]: r["value"] for r in
             db.execute("SELECT key,value FROM goals WHERE user_id=?", (uid,)).fetchall()}
    return {
        "id": uid, "name": user["name"], "email": user["email"],
        "goals": goals
    }

@router.post("/profile")
def update_profile(req: dict, user=Depends(get_optional_user), db=Depends(get_db)):
    if not user:
        raise HTTPException(401, "Not authenticated")
    uid = user["id"]
    # Update name
    if "name" in req:
        db.execute("UPDATE users SET name=? WHERE id=?", (req["name"], uid))
    # Update goals/preferences
    goal_keys = ["height_cm","weight_kg","age","gender","calories_target","protein_target",
                 "steps_target","nutrition_enabled","weekly_workouts_target","max_hr_manual",
                 "race_date","race_name","carbs_target","fat_target"]
    for key in goal_keys:
        if key in req:
            db.execute("""
                INSERT INTO goals (user_id, key, value) VALUES (?,?,?)
                ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value
            """, (uid, key, str(req[key])))
    db.commit()
    return {"message": "Профиль обновлён"}


@router.get("/admin/stats")
def admin_stats(secret: str = "", db=Depends(get_db)):
    """Admin stats — access with ?secret=YOUR_SECRET"""
    import os
    admin_secret = os.environ.get("ADMIN_SECRET", "smartfit2026")
    if secret != admin_secret:
        raise HTTPException(403, "Forbidden")

    users = db.execute("""
        SELECT id, name, email, created_at,
          (SELECT COUNT(*) FROM workouts WHERE user_id=users.id) as workout_count,
          (SELECT COUNT(*) FROM nutrition WHERE user_id=users.id) as meal_count,
          (SELECT MAX(date) FROM workouts WHERE user_id=users.id) as last_workout,
          (SELECT email FROM garmin_accounts WHERE user_id=users.id) as garmin_email,
          (SELECT value FROM goals WHERE user_id=users.id AND key='race_name') as race_name
        FROM users ORDER BY id DESC
    """).fetchall()

    total = len(users)
    with_garmin = sum(1 for u in users if u["garmin_email"])
    with_workouts = sum(1 for u in users if u["workout_count"] > 0)
    active_7d = db.execute("""
        SELECT COUNT(DISTINCT user_id) as cnt FROM workouts
        WHERE date >= date('now', '-7 days')
    """).fetchone()["cnt"]

    return {
        "summary": {
            "total_users": total,
            "with_garmin": with_garmin,
            "with_workouts": with_workouts,
            "active_last_7d": active_7d,
        },
        "users": [dict(u) for u in users]
    }


@router.get("/admin/user/{user_id}")
def admin_user_detail(user_id: int, secret: str = "", db=Depends(get_db)):
    import os
    if secret != os.environ.get("ADMIN_SECRET", "smartfit2026"):
        raise HTTPException(403, "Forbidden")
    user = db.execute("SELECT id,name,email,created_at FROM users WHERE id=?", (user_id,)).fetchone()
    if not user: raise HTTPException(404, "User not found")
    workouts = db.execute("""
        SELECT date,type,distance_km,duration_min,calories,avg_hr,source
        FROM workouts WHERE user_id=? ORDER BY date DESC LIMIT 20
    """, (user_id,)).fetchall()
    goals = {r["key"]:r["value"] for r in
             db.execute("SELECT key,value FROM goals WHERE user_id=?", (user_id,)).fetchall()}
    health = db.execute("""
        SELECT date,steps,resting_hr,sleep_hours,weight_kg,hrv
        FROM health_metrics WHERE user_id=? ORDER BY date DESC LIMIT 7
    """, (user_id,)).fetchall()
    return {
        "user": dict(user), "goals": goals,
        "recent_workouts": [dict(w) for w in workouts],
        "recent_health": [dict(h) for h in health],
    }

@router.delete("/admin/user/{user_id}")
def admin_delete_user(user_id: int, secret: str = "", db=Depends(get_db)):
    import os
    if secret != os.environ.get("ADMIN_SECRET", "smartfit2026"):
        raise HTTPException(403, "Forbidden")
    for table in ["workouts","nutrition","health_metrics","goals","user_plans","garmin_accounts"]:
        db.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
    db.execute("DELETE FROM users WHERE id=?", (user_id,))
    db.commit()
    return {"message": f"User {user_id} deleted"}

@router.post("/admin/broadcast")
def admin_broadcast(req: dict, secret: str = "", db=Depends(get_db)):
    """Store a broadcast message shown to all users on next login"""
    import os
    if secret != os.environ.get("ADMIN_SECRET", "smartfit2026"):
        raise HTTPException(403, "Forbidden")
    msg = req.get("message","")
    db.execute("""
        INSERT INTO goals (user_id, key, value) VALUES (0,'broadcast',?)
        ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value
    """, (msg,))
    db.commit()
    return {"message": "Broadcast set"}
