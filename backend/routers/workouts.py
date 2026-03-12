import json as _json
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from database import get_db
from routers.auth import get_optional_user
from datetime import date, timedelta

router = APIRouter()
import os, requests as _requests, json as _json

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


@router.post("/analyze/{workout_id}")
def analyze_workout(workout_id: int, user=Depends(get_optional_user), db=Depends(get_db)):
    """Generate AI analysis for a specific workout"""
    uid = get_uid(user)
    api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    if not api_key:
        raise HTTPException(400, "API key not configured")

    w = db.execute(
        "SELECT * FROM workouts WHERE id=? AND user_id=?", (workout_id, uid)
    ).fetchone()
    if not w:
        raise HTTPException(404, "Workout not found")
    w = dict(w)

    # Get user's average HR for this workout type (last 30 days)
    avg_row = db.execute("""
        SELECT ROUND(AVG(avg_hr)) as avg, COUNT(*) as cnt
        FROM workouts WHERE user_id=? AND type=? AND avg_hr IS NOT NULL
        AND date >= date('now','-30 days') AND id != ?
    """, (uid, w["type"], workout_id)).fetchone()
    typical_hr = avg_row["avg"] if avg_row else None
    recent_count = avg_row["cnt"] if avg_row else 0

    # Build context
    hr_note = ""
    if w.get("avg_hr") and typical_hr:
        diff = round(w["avg_hr"] - typical_hr)
        if abs(diff) >= 3:
            hr_note = f"Средний пульс {'+' if diff>0 else ''}{diff} bpm от нормы ({typical_hr} bpm обычно)."

    prompt = f"""Ты персональный тренер. Напиши короткий разбор тренировки на русском — 2-3 предложения максимум. Конкретно и полезно.

Тренировка:
- Тип: {w['type']}
- Дата: {w['date']}
- Дистанция: {w.get('distance_km', '—')} км
- Время: {w.get('duration_min', '—')} мин
- Темп: {w.get('avg_pace', '—')} мин/км
- Средний пульс: {w.get('avg_hr', '—')} bpm
- Макс пульс: {w.get('max_hr', '—')} bpm
- Каденс: {w.get('avg_cadence', '—')} шаг/мин
- Набор высоты: {w.get('elevation_gain', '—')} м
- Калории: {w.get('calories', '—')}
{hr_note}

Формат: сначала факт о тренировке, потом оценка качества, потом совет на следующую тренировку."""

    resp = _requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 200,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=20)
    resp.raise_for_status()
    analysis = resp.json()["content"][0]["text"]

    # Save to DB
    db.execute("UPDATE workouts SET ai_analysis=? WHERE id=?", (analysis, workout_id))
    db.commit()
    return {"analysis": analysis}


@router.get("/hub")
def get_training_hub(period: str = "month", user=Depends(get_optional_user), db=Depends(get_db)):
    """Training Hub: all workouts with stats and comparison"""
    uid = get_uid(user)
    days = {"week": 7, "month": 30, "3month": 90, "year": 365}.get(period, 30)

    workouts = db.execute("""
        SELECT * FROM workouts WHERE user_id=?
        AND date >= date('now', ? || ' days')
        ORDER BY date DESC, id DESC
    """, (uid, f"-{days}")).fetchall()
    workouts = [dict(w) for w in workouts]

    # Previous period for comparison
    prev = db.execute("""
        SELECT type,
               ROUND(AVG(avg_hr)) as avg_hr,
               ROUND(AVG(distance_km),2) as avg_dist,
               ROUND(AVG(avg_pace)) as avg_pace,
               COUNT(*) as count
        FROM workouts WHERE user_id=?
        AND date >= date('now', ? || ' days')
        AND date < date('now', ? || ' days')
        GROUP BY type
    """, (uid, f"-{days*2}", f"-{days}")).fetchall()
    prev_stats = {r["type"]: dict(r) for r in prev}

    # Summary by type
    summary = db.execute("""
        SELECT type,
               COUNT(*) as count,
               ROUND(SUM(distance_km),1) as total_km,
               ROUND(SUM(duration_min)) as total_min,
               ROUND(SUM(calories)) as total_cal,
               ROUND(AVG(avg_hr)) as avg_hr,
               ROUND(AVG(distance_km),2) as avg_dist
        FROM workouts WHERE user_id=?
        AND date >= date('now', ? || ' days')
        GROUP BY type ORDER BY count DESC
    """, (uid, f"-{days}")).fetchall()

    return {
        "workouts": workouts,
        "summary": [dict(r) for r in summary],
        "prev_stats": prev_stats,
        "period": period
    }


@router.get("/plan")
def get_plan(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    row = db.execute(
        "SELECT plan_json, race_date, race_name FROM user_plans WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (uid,)
    ).fetchone()
    if not row:
        return {"plan": None, "race_date": None, "race_name": None}
    return {"plan": _json.loads(row["plan_json"]) if row["plan_json"] else None,
            "race_date": row["race_date"], "race_name": row["race_name"]}

@router.post("/save-plan")
def save_plan(req: dict, user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    plan_json = _json.dumps(req.get("plan", []), ensure_ascii=False)
    race_date = req.get("race_date", "")
    race_name = req.get("race_name", "")
    # Calculate weeks until race
    existing = db.execute("SELECT id FROM user_plans WHERE user_id=?", (uid,)).fetchone()
    if existing:
        db.execute("""UPDATE user_plans SET plan_json=?, race_date=?, race_name=? WHERE user_id=?""",
                   (plan_json, race_date, race_name, uid))
    else:
        db.execute("""INSERT INTO user_plans (user_id, plan_json, race_date, race_name) VALUES (?,?,?,?)""",
                   (uid, plan_json, race_date, race_name))
    db.commit()
    return {"message": "Plan saved"}
