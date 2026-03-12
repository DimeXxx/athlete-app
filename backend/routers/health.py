from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from database import get_db
from routers.auth import get_optional_user
from datetime import date, timedelta

router = APIRouter()

class HealthMetric(BaseModel):
    date: Optional[str] = None
    steps: Optional[int] = None
    resting_hr: Optional[int] = None
    sleep_hours: Optional[float] = None
    weight_kg: Optional[float] = None
    vo2max: Optional[float] = None
    hrv: Optional[int] = None

def get_uid(user):
    return user["id"] if user else 1

def steps_to_calories(steps: int, weight_kg: float = 70) -> int:
    """Estimate calories burned from steps"""
    # ~0.04 kcal per step per 70kg, scales with weight
    return round(steps * 0.04 * (weight_kg / 70))

@router.get("/today")
def get_today(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    today = date.today().isoformat()
    row = db.execute(
        "SELECT * FROM health_metrics WHERE user_id=? AND date=?", (uid, today)
    ).fetchone()
    goals = {r["key"]: r["value"] for r in
             db.execute("SELECT key, value FROM goals WHERE user_id=?", (uid,)).fetchall()}
    
    metrics = dict(row) if row else {}
    # Add estimated calories from steps
    if metrics.get("steps"):
        weight = float(goals.get("weight_kg", 70))
        metrics["steps_calories"] = steps_to_calories(metrics["steps"], weight)
    
    return {"metrics": metrics, "date": today, "goals": goals}

@router.get("/steps")
def get_steps(period: str = "week", user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    days_map = {"week": 7, "month": 30, "3month": 90, "year": 365}
    days = days_map.get(period, 7)
    
    rows = db.execute("""
        SELECT date, steps, resting_hr, weight_kg
        FROM health_metrics
        WHERE user_id=? AND date >= date('now', ? || ' days') AND steps IS NOT NULL
        ORDER BY date DESC
    """, (uid, f"-{days}")).fetchall()
    
    summary = db.execute("""
        SELECT 
            ROUND(AVG(steps)) as avg_steps,
            MAX(steps) as max_steps,
            MIN(steps) as min_steps,
            COUNT(*) as days_tracked,
            ROUND(AVG(resting_hr),1) as avg_rhr
        FROM health_metrics
        WHERE user_id=? AND date >= date('now', ? || ' days') AND steps IS NOT NULL
    """, (uid, f"-{days}")).fetchone()
    
    goals = {r["key"]: r["value"] for r in
             db.execute("SELECT key, value FROM goals WHERE user_id=?", (uid,)).fetchall()}
    weight = float(goals.get("weight_kg", 70))
    steps_goal = int(goals.get("steps_target", 10000))
    
    result = []
    for r in rows:
        d = dict(r)
        if d["steps"]:
            d["calories"] = steps_to_calories(d["steps"], weight)
        result.append(d)
    
    s = dict(summary) if summary else {}
    if s.get("avg_steps"):
        s["avg_calories"] = steps_to_calories(int(s["avg_steps"]), weight)
    
    return {
        "days": result,
        "summary": s,
        "steps_goal": steps_goal,
        "period": period
    }

@router.post("/")
def log_metric(metric: HealthMetric, user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    metric_date = metric.date or date.today().isoformat()
    db.execute("""
        INSERT INTO health_metrics (user_id, date, steps, resting_hr, sleep_hours, weight_kg, vo2max, hrv)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(user_id, date) DO UPDATE SET
            steps = COALESCE(excluded.steps, steps),
            resting_hr = COALESCE(excluded.resting_hr, resting_hr),
            sleep_hours = COALESCE(excluded.sleep_hours, sleep_hours),
            weight_kg = COALESCE(excluded.weight_kg, weight_kg),
            vo2max = COALESCE(excluded.vo2max, vo2max),
            hrv = COALESCE(excluded.hrv, hrv)
    """, (uid, metric_date, metric.steps, metric.resting_hr, metric.sleep_hours,
          metric.weight_kg, metric.vo2max, metric.hrv))
    db.commit()
    return {"message": "Saved!", "date": metric_date}
