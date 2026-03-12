from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from database import get_db
from routers.auth import get_optional_user
from datetime import date, timedelta

router = APIRouter()

class WorkoutCreate(BaseModel):
    date: str
    type: str
    duration_min: Optional[float] = None
    distance_km: Optional[float] = None
    calories: Optional[int] = None
    avg_hr: Optional[int] = None
    notes: Optional[str] = None

def get_uid(user):
    return user["id"] if user else 0  # 0 = guest, no real data

def get_date_range(period: str):
    today = date.today()
    if period == "week":
        return (today - timedelta(days=7)).isoformat(), today.isoformat()
    elif period == "month":
        return (today - timedelta(days=30)).isoformat(), today.isoformat()
    elif period == "3month":
        return (today - timedelta(days=90)).isoformat(), today.isoformat()
    elif period == "6month":
        return (today - timedelta(days=180)).isoformat(), today.isoformat()
    elif period == "year":
        return (today - timedelta(days=365)).isoformat(), today.isoformat()
    else:  # "all"
        return "2000-01-01", today.isoformat()

@router.get("/")
def get_workouts(limit: int = 50, user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    rows = db.execute(
        "SELECT * FROM workouts WHERE user_id=? ORDER BY date DESC LIMIT ?",
        (uid, limit)
    ).fetchall()
    return [dict(r) for r in rows]

@router.get("/today")
def get_today(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    today = date.today().isoformat()
    rows = db.execute(
        "SELECT * FROM workouts WHERE user_id=? AND date=? ORDER BY created_at DESC",
        (uid, today)
    ).fetchall()
    return [dict(r) for r in rows]

@router.get("/swim/stats")
def get_swim_stats(period: str = "all", user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    start, end = get_date_range(period)
    rows = db.execute("""
        SELECT date, ROUND(distance_km*1000) as distance_m, duration_min,
               ROUND(duration_min / (distance_km*10), 1) as pace_per_100m,
               avg_hr, calories
        FROM workouts
        WHERE user_id=? AND type='Swimming' AND date BETWEEN ? AND ?
        ORDER BY date DESC LIMIT 50
    """, (uid, start, end)).fetchall()
    total = db.execute("""
        SELECT COUNT(*) as count,
               ROUND(SUM(distance_km),1) as total_km,
               ROUND(AVG(distance_km*1000)) as avg_m,
               ROUND(AVG(avg_hr)) as avg_hr,
               MAX(distance_km*1000) as best_m
        FROM workouts
        WHERE user_id=? AND type='Swimming' AND date BETWEEN ? AND ?
    """, (uid, start, end)).fetchone()
    return {"sessions": [dict(r) for r in rows], "total": dict(total), "period": period}

@router.get("/run/stats")
def get_run_stats(period: str = "all", user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    start, end = get_date_range(period)
    rows = db.execute("""
        SELECT date, ROUND(distance_km, 2) as distance_km, duration_min,
               ROUND(duration_min / NULLIF(distance_km,0), 1) as pace_per_km,
               avg_hr, calories
        FROM workouts
        WHERE user_id=? AND type IN ('Running','RunLong','trail_running','running')
          AND date BETWEEN ? AND ?
        ORDER BY date DESC LIMIT 50
    """, (uid, start, end)).fetchall()
    total = db.execute("""
        SELECT COUNT(*) as count,
               ROUND(SUM(distance_km),1) as total_km,
               ROUND(AVG(distance_km),1) as avg_km,
               MAX(distance_km) as best_km,
               ROUND(AVG(avg_hr)) as avg_hr
        FROM workouts
        WHERE user_id=? AND type IN ('Running','RunLong','trail_running','running')
          AND date BETWEEN ? AND ?
    """, (uid, start, end)).fetchone()
    return {"sessions": [dict(r) for r in rows], "total": dict(total), "period": period}

@router.get("/history")
def get_history(period: str = "month", type: str = "all",
                user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    start, end = get_date_range(period)
    type_filter = "" if type == "all" else f"AND type='{type}'"
    rows = db.execute(f"""
        SELECT id, date, type, distance_km, duration_min, avg_hr, calories, source
        FROM workouts
        WHERE user_id=? AND date BETWEEN ? AND ? {type_filter}
        ORDER BY date DESC
    """, (uid, start, end)).fetchall()
    return [dict(r) for r in rows]

@router.post("/")
def create_workout(workout: WorkoutCreate, user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    db.execute("""
        INSERT INTO workouts (user_id, date, type, duration_min, distance_km, calories, avg_hr, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (uid, workout.date, workout.type, workout.duration_min,
          workout.distance_km, workout.calories, workout.avg_hr, workout.notes))
    db.commit()
    id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": id, "message": "Workout saved!"}

@router.delete("/{workout_id}")
def delete_workout(workout_id: int, user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    db.execute("DELETE FROM workouts WHERE id=? AND user_id=?", (workout_id, uid))
    db.commit()
    return {"message": "Deleted"}
