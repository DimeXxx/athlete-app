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
    return user["id"] if user else 0  # 0 = guest, no real data

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

@router.get("/dashboard")
def get_dashboard(period: str = "week", user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    days_map = {"day": 1, "week": 7, "month": 30, "year": 365}
    days = days_map.get(period, 7)
    since = (date.today() - timedelta(days=days)).isoformat()
    today = date.today().isoformat()

    goals = {r["key"]: r["value"] for r in
             db.execute("SELECT key, value FROM goals WHERE user_id=?", (uid,)).fetchall()}
    weight = float(goals.get("weight_kg", 70))

    # Workouts summary
    workouts = db.execute("""
        SELECT type, COUNT(*) as cnt,
               ROUND(SUM(distance_km),1) as km,
               ROUND(SUM(duration_min)) as mins,
               ROUND(SUM(calories)) as cals,
               ROUND(AVG(avg_hr)) as avg_hr
        FROM workouts
        WHERE user_id=? AND date >= ? AND date <= ?
        GROUP BY type ORDER BY cnt DESC
    """, (uid, since, today)).fetchall()

    totals = db.execute("""
        SELECT COUNT(*) as total_workouts,
               ROUND(SUM(distance_km),1) as total_km,
               ROUND(SUM(duration_min)) as total_mins,
               ROUND(SUM(calories)) as workout_cals
        FROM workouts WHERE user_id=? AND date >= ? AND date <= ?
    """, (uid, since, today)).fetchone()

    # Nutrition summary
    nutrition = db.execute("""
        SELECT COUNT(DISTINCT date) as days_logged,
               ROUND(AVG(daily_cal)) as avg_cal,
               ROUND(AVG(daily_prot),1) as avg_prot,
               ROUND(SUM(daily_cal)) as total_cal
        FROM (
            SELECT date,
                   SUM(calories) as daily_cal,
                   SUM(protein_g) as daily_prot
            FROM nutrition
            WHERE user_id=? AND date >= ? AND date <= ?
            GROUP BY date
        )
    """, (uid, since, today)).fetchone()

    # Steps summary
    steps = db.execute("""
        SELECT COUNT(*) as days_tracked,
               ROUND(AVG(steps)) as avg_steps,
               ROUND(SUM(steps)) as total_steps,
               MAX(steps) as best_steps
        FROM health_metrics
        WHERE user_id=? AND date >= ? AND date <= ? AND steps IS NOT NULL
    """, (uid, since, today)).fetchone()

    # Daily chart data
    chart = db.execute("""
        SELECT h.date,
               COALESCE(h.steps, 0) as steps,
               COALESCE(n.cal, 0) as calories,
               COALESCE(w.km, 0) as km,
               COALESCE(w.mins, 0) as mins
        FROM health_metrics h
        LEFT JOIN (
            SELECT date, ROUND(SUM(calories)) as cal
            FROM nutrition WHERE user_id=?
            GROUP BY date
        ) n ON h.date = n.date
        LEFT JOIN (
            SELECT date, ROUND(SUM(distance_km),1) as km,
                   ROUND(SUM(duration_min)) as mins
            FROM workouts WHERE user_id=?
            GROUP BY date
        ) w ON h.date = w.date
        WHERE h.user_id=? AND h.date >= ? AND h.date <= ?
        ORDER BY h.date ASC
    """, (uid, uid, uid, since, today)).fetchall()

    # Steps calories estimate
    avg_steps = steps["avg_steps"] or 0
    steps_cals_per_day = round(avg_steps * 0.04 * (weight / 70))

    return {
        "period": period,
        "workouts": [dict(w) for w in workouts],
        "totals": dict(totals) if totals else {},
        "nutrition": dict(nutrition) if nutrition else {},
        "steps": dict(steps) if steps else {},
        "steps_cals_per_day": steps_cals_per_day,
        "chart": [dict(r) for r in chart],
        "goals": goals,
    }


@router.get("/recovery")
def get_recovery_data(user=Depends(get_optional_user), db=Depends(get_db)):
    """Full recovery & injury risk data"""
    uid = get_uid(user)
    from datetime import date, timedelta
    today = date.today()

    # Health metrics last 14 days
    metrics = db.execute("""
        SELECT date, hrv, sleep_hours, resting_hr, steps, weight_kg
        FROM health_metrics WHERE user_id=? AND date >= ?
        ORDER BY date DESC
    """, (uid, str(today - timedelta(days=14)))).fetchall()
    metrics = [dict(m) for m in metrics]

    # Workouts last 14 days
    workouts = db.execute("""
        SELECT date, type, duration_min, distance_km, calories, avg_hr, avg_pace
        FROM workouts WHERE user_id=? AND date >= ?
        ORDER BY date DESC
    """, (uid, str(today - timedelta(days=14)))).fetchall()
    workouts = [dict(w) for w in workouts]

    # Pain checkins
    pain_rows = db.execute("""
        SELECT key, value, created_at FROM goals
        WHERE user_id=? AND key LIKE 'pain_%'
    """, (uid,)).fetchall()
    pain_data = [dict(p) for p in pain_rows]

    # Evening checkins
    checkin_rows = db.execute("""
        SELECT key, value FROM goals
        WHERE user_id=? AND key LIKE 'checkin_%'
        ORDER BY key DESC LIMIT 20
    """, (uid,)).fetchall()
    checkins = [dict(c) for c in checkin_rows]

    # Calculate load this week vs last week
    this_week_start = str(today - timedelta(days=7))
    last_week_start = str(today - timedelta(days=14))

    load_this = sum(w.get('duration_min',0) or 0 for w in workouts if w['date'] >= this_week_start)
    load_last = sum(w.get('duration_min',0) or 0 for w in workouts if w['date'] < this_week_start)

    load_change_pct = round((load_this - load_last) / max(load_last, 1) * 100) if load_last > 0 else 0

    # Latest health
    latest = metrics[0] if metrics else {}
    hrv = latest.get('hrv')
    sleep = latest.get('sleep_hours')
    rhr = latest.get('resting_hr')

    # HRV baseline (avg last 7 days)
    recent_hrv = [m['hrv'] for m in metrics[:7] if m.get('hrv')]
    hrv_baseline = round(sum(recent_hrv)/len(recent_hrv)) if recent_hrv else None

    # Pain zones from goals
    pain_zones = {}
    for p in pain_data:
        zone = p['key'].replace('pain_','')
        pain_zones[zone] = int(p.get('value', 0) or 0)

    return {
        "load_this_week": load_this,
        "load_last_week": load_last,
        "load_change_pct": load_change_pct,
        "hrv": hrv,
        "hrv_baseline": hrv_baseline,
        "sleep": sleep,
        "rhr": rhr,
        "pain_zones": pain_zones,
        "metrics": metrics[:7],
        "workouts": workouts[:10],
        "checkins": checkins[:5],
    }


@router.post("/pain-checkin")
def save_pain_checkin(req: dict, user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    for zone, val in req.get('pain', {}).items():
        key = f"pain_{zone}"
        db.execute("""
            INSERT INTO goals (user_id, key, value) VALUES (?,?,?)
            ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value
        """, (uid, key, str(val)))
    for field, val in req.get('checkin', {}).items():
        from datetime import date
        key = f"checkin_{date.today()}_{field}"
        db.execute("""
            INSERT INTO goals (user_id, key, value) VALUES (?,?,?)
            ON CONFLICT(user_id,key) DO UPDATE SET value=excluded.value
        """, (uid, key, str(val)))
    db.commit()
    return {"message": "Saved"}
