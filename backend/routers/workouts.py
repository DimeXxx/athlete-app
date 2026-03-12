from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from database import get_db
from routers.auth import get_optional_user
from datetime import date

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
    return user["id"] if user else 1

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
def get_swim_stats(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    rows = db.execute("""
        SELECT date, ROUND(distance_km*1000) as distance_m, duration_min,
               ROUND(duration_min / (distance_km*10), 1) as pace_per_100m
        FROM workouts WHERE user_id=? AND type='Swimming'
        ORDER BY date DESC LIMIT 20
    """, (uid,)).fetchall()
    total = db.execute("""
        SELECT COUNT(*) as count, ROUND(SUM(distance_km),1) as total_km
        FROM workouts WHERE user_id=? AND type='Swimming'
    """, (uid,)).fetchone()
    return {"sessions": [dict(r) for r in rows], "total": dict(total)}

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

@router.get("/run/stats")
def get_run_stats(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    rows = db.execute("""
        SELECT date, ROUND(distance_km, 2) as distance_km, duration_min,
               ROUND(duration_min / distance_km, 1) as pace_per_km
        FROM workouts WHERE user_id=? AND type IN ('Running','trail_running','running')
        ORDER BY date DESC LIMIT 20
    """, (uid,)).fetchall()
    total = db.execute("""
        SELECT COUNT(*) as count, ROUND(SUM(distance_km),1) as total_km,
               ROUND(AVG(distance_km),1) as avg_km
        FROM workouts WHERE user_id=? AND type IN ('Running','trail_running','running')
    """, (uid,)).fetchone()
    return {"sessions": [dict(r) for r in rows], "total": dict(total)}
