from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional, List
from database import get_db
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

class WorkoutUpdate(BaseModel):
    duration_min: Optional[float] = None
    distance_km: Optional[float] = None
    calories: Optional[int] = None
    avg_hr: Optional[int] = None
    notes: Optional[str] = None

@router.get("/")
def get_workouts(limit: int = 50, offset: int = 0, db=Depends(get_db)):
    rows = db.execute(
        "SELECT * FROM workouts ORDER BY date DESC LIMIT ? OFFSET ?",
        (limit, offset)
    ).fetchall()
    return [dict(r) for r in rows]

@router.get("/today")
def get_today_workouts(db=Depends(get_db)):
    today = date.today().isoformat()
    rows = db.execute(
        "SELECT * FROM workouts WHERE date = ? ORDER BY created_at DESC",
        (today,)
    ).fetchall()
    return [dict(r) for r in rows]

@router.get("/week")
def get_week_workouts(db=Depends(get_db)):
    rows = db.execute("""
        SELECT * FROM workouts
        WHERE date >= date('now', 'weekday 1', '-7 days')
        ORDER BY date DESC
    """).fetchall()
    return [dict(r) for r in rows]

@router.get("/stats")
def get_workout_stats(db=Depends(get_db)):
    stats = db.execute("""
        SELECT
            type,
            COUNT(*) as count,
            ROUND(SUM(duration_min)/60, 1) as total_hours,
            ROUND(SUM(distance_km), 1) as total_km,
            ROUND(AVG(avg_hr)) as avg_hr
        FROM workouts
        WHERE date >= date('now', '-365 days')
        GROUP BY type
        ORDER BY count DESC
    """).fetchall()

    monthly = db.execute("""
        SELECT
            strftime('%Y-%m', date) as month,
            type,
            COUNT(*) as count,
            ROUND(SUM(distance_km), 1) as km
        FROM workouts
        WHERE date >= date('now', '-12 months')
        GROUP BY month, type
        ORDER BY month DESC
    """).fetchall()

    return {
        "by_type": [dict(r) for r in stats],
        "monthly": [dict(r) for r in monthly]
    }

@router.get("/swim/stats")
def get_swim_stats(db=Depends(get_db)):
    rows = db.execute("""
        SELECT date, distance_km*1000 as distance_m, duration_min,
               ROUND(duration_min / (distance_km*10), 1) as pace_per_100m
        FROM workouts
        WHERE type = 'Swimming'
        ORDER BY date DESC
        LIMIT 20
    """).fetchall()
    total = db.execute("""
        SELECT COUNT(*) as count, ROUND(SUM(distance_km), 1) as total_km
        FROM workouts WHERE type = 'Swimming'
    """).fetchone()
    return {"sessions": [dict(r) for r in rows], "total": dict(total)}

@router.post("/")
def create_workout(workout: WorkoutCreate, db=Depends(get_db)):
    db.execute("""
        INSERT INTO workouts (date, type, duration_min, distance_km, calories, avg_hr, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (workout.date, workout.type, workout.duration_min,
          workout.distance_km, workout.calories, workout.avg_hr, workout.notes))
    db.commit()
    id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": id, "message": "Workout saved!"}

@router.delete("/{workout_id}")
def delete_workout(workout_id: int, db=Depends(get_db)):
    db.execute("DELETE FROM workouts WHERE id = ?", (workout_id,))
    db.commit()
    return {"message": "Deleted"}
