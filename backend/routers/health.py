from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from database import get_db
from datetime import date

router = APIRouter()

class HealthMetric(BaseModel):
    date: Optional[str] = None
    steps: Optional[int] = None
    resting_hr: Optional[int] = None
    sleep_hours: Optional[float] = None
    sleep_deep_min: Optional[int] = None
    sleep_rem_min: Optional[int] = None
    weight_kg: Optional[float] = None
    vo2max: Optional[float] = None
    hrv: Optional[int] = None

@router.get("/today")
def get_today(db=Depends(get_db)):
    today = date.today().isoformat()
    row = db.execute("SELECT * FROM health_metrics WHERE date = ?", (today,)).fetchone()
    goals = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM goals").fetchall()}
    return {
        "metrics": dict(row) if row else {},
        "date": today,
        "goals": goals
    }

@router.get("/summary")
def get_summary(db=Depends(get_db)):
    """Dashboard summary: last 30 days averages"""
    row = db.execute("""
        SELECT
            ROUND(AVG(steps)) as avg_steps,
            ROUND(AVG(resting_hr), 1) as avg_rhr,
            ROUND(AVG(sleep_hours), 1) as avg_sleep,
            ROUND(AVG(weight_kg), 1) as avg_weight,
            ROUND(AVG(vo2max), 1) as avg_vo2max,
            ROUND(AVG(hrv)) as avg_hrv,
            MAX(steps) as max_steps,
            MIN(resting_hr) as min_rhr,
            COUNT(*) as days_logged
        FROM health_metrics
        WHERE date >= date('now', '-30 days')
    """).fetchone()

    recent_weight = db.execute("""
        SELECT weight_kg, date FROM health_metrics
        WHERE weight_kg IS NOT NULL
        ORDER BY date DESC LIMIT 1
    """).fetchone()

    return {
        "last_30_days": dict(row) if row else {},
        "latest_weight": dict(recent_weight) if recent_weight else {}
    }

@router.get("/history")
def get_history(metric: str = "steps", days: int = 30, db=Depends(get_db)):
    valid = ["steps", "resting_hr", "sleep_hours", "weight_kg", "vo2max", "hrv"]
    if metric not in valid:
        metric = "steps"
    rows = db.execute(f"""
        SELECT date, {metric} as value
        FROM health_metrics
        WHERE date >= date('now', '-{days} days') AND {metric} IS NOT NULL
        ORDER BY date
    """).fetchall()
    return [dict(r) for r in rows]

@router.post("/")
def log_metric(metric: HealthMetric, db=Depends(get_db)):
    metric_date = metric.date or date.today().isoformat()
    db.execute("""
        INSERT INTO health_metrics (date, steps, resting_hr, sleep_hours,
            sleep_deep_min, sleep_rem_min, weight_kg, vo2max, hrv)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(date) DO UPDATE SET
            steps = COALESCE(excluded.steps, steps),
            resting_hr = COALESCE(excluded.resting_hr, resting_hr),
            sleep_hours = COALESCE(excluded.sleep_hours, sleep_hours),
            sleep_deep_min = COALESCE(excluded.sleep_deep_min, sleep_deep_min),
            sleep_rem_min = COALESCE(excluded.sleep_rem_min, sleep_rem_min),
            weight_kg = COALESCE(excluded.weight_kg, weight_kg),
            vo2max = COALESCE(excluded.vo2max, vo2max),
            hrv = COALESCE(excluded.hrv, hrv)
    """, (metric_date, metric.steps, metric.resting_hr, metric.sleep_hours,
          metric.sleep_deep_min, metric.sleep_rem_min, metric.weight_kg,
          metric.vo2max, metric.hrv))
    db.commit()
    return {"message": "Metric saved!", "date": metric_date}
