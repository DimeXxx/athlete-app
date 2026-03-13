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


@router.post("/pain-log")
def save_pain_log(req: dict, user=Depends(get_optional_user), db=Depends(get_db)):
    """Save body check pain data to pain_log table"""
    from datetime import date as dt
    uid = get_uid(user)
    today = dt.today().isoformat()
    pain = req.get("pain", {})
    try:
        for zone, level in pain.items():
            if int(level) > 0:
                db.execute(
                    "INSERT INTO pain_log (user_id, date, zone, pain_level) VALUES (?,?,?,?)",
                    (uid, today, zone, int(level))
                )
        db.commit()
    except Exception as e:
        pass
    return {"ok": True}


@router.get("/performance")
def get_performance(user=Depends(get_optional_user), db=Depends(get_db)):
    """Performance Score + all metrics for the Performance screen"""
    from datetime import date as dt, timedelta
    import json, math
    uid = get_uid(user)
    today = dt.today().isoformat()
    d30 = (dt.today() - timedelta(days=30)).isoformat()
    d60 = (dt.today() - timedelta(days=60)).isoformat()
    d7  = (dt.today() - timedelta(days=7)).isoformat()

    # ── Goals ──────────────────────────────────────────────
    goals = {r["key"]: r["value"] for r in
             db.execute("SELECT key,value FROM goals WHERE user_id=?", (uid,)).fetchall()}
    prot_goal = int(goals.get("protein_target", 150))

    # ── VO2max ─────────────────────────────────────────────
    vo2_row = db.execute("""
        SELECT vo2max, date FROM health_metrics
        WHERE user_id=? AND vo2max IS NOT NULL ORDER BY date DESC LIMIT 1
    """, (uid,)).fetchone()
    vo2max_now = float(vo2_row["vo2max"]) if vo2_row else None
    vo2_date   = vo2_row["date"] if vo2_row else None

    vo2_old = db.execute("""
        SELECT vo2max FROM health_metrics
        WHERE user_id=? AND vo2max IS NOT NULL AND date <= ? ORDER BY date DESC LIMIT 1
    """, (uid, d30)).fetchone()
    vo2max_old = float(vo2_old["vo2max"]) if vo2_old else vo2max_now
    vo2_trend = round((vo2max_now or 0) - (vo2max_old or 0), 1) if vo2max_now and vo2max_old else 0

    # ── Running efficiency: avg pace / avg HR in Z2 runs ──
    def parse_pace(pace_str):
        """'5:45' → seconds"""
        if not pace_str: return None
        try:
            parts = str(pace_str).split(":")
            return int(parts[0])*60 + int(parts[1])
        except: return None

    def secs_to_pace(s):
        if not s: return None
        return f"{int(s)//60}:{int(s)%60:02d}"

    runs_now = db.execute("""
        SELECT avg_pace, avg_hr, distance_km, duration_min FROM workouts
        WHERE user_id=? AND type IN ('Running','RunLong') AND date >= ?
        AND avg_hr IS NOT NULL AND avg_hr BETWEEN 120 AND 155
        ORDER BY date DESC
    """, (uid, d30)).fetchall()

    runs_old = db.execute("""
        SELECT avg_pace, avg_hr, distance_km, duration_min FROM workouts
        WHERE user_id=? AND type IN ('Running','RunLong') AND date >= ? AND date < ?
        AND avg_hr IS NOT NULL AND avg_hr BETWEEN 120 AND 155
        ORDER BY date DESC
    """, (uid, d60, d30)).fetchall()

    def avg_z2(rows):
        paces = [parse_pace(r["avg_pace"]) for r in rows if r["avg_pace"]]
        hrs   = [r["avg_hr"] for r in rows if r["avg_hr"]]
        return (
            round(sum(paces)/len(paces)) if paces else None,
            round(sum(hrs)/len(hrs))     if hrs   else None
        )

    z2_pace_now, z2_hr_now = avg_z2(runs_now)
    z2_pace_old, z2_hr_old = avg_z2(runs_old)

    run_eff_change = None
    if z2_pace_now and z2_pace_old and z2_hr_now and z2_hr_old:
        eff_now = z2_hr_now / z2_pace_now * 100
        eff_old = z2_hr_old / z2_pace_old * 100
        run_eff_change = round((eff_now - eff_old) / eff_old * 100, 1) if eff_old else 0
    aerobic_improvement = (z2_pace_old - z2_pace_now) if z2_pace_now and z2_pace_old else None  # positive = faster

    # ── Consistency ────────────────────────────────────────
    weeks_data = []
    for w in range(4):
        wstart = (dt.today() - timedelta(days=(w+1)*7)).isoformat()
        wend   = (dt.today() - timedelta(days=w*7)).isoformat()
        cnt = db.execute("""
            SELECT COUNT(*) as c FROM workouts
            WHERE user_id=? AND date >= ? AND date < ?
        """, (uid, wstart, wend)).fetchone()["c"]
        weeks_data.append(cnt)
    avg_per_week = round(sum(weeks_data) / len(weeks_data), 1) if weeks_data else 0

    # Consecutive weeks with >= 3 workouts
    consec_weeks = 0
    for cnt in weeks_data:
        if cnt >= 3: consec_weeks += 1
        else: break

    # Missed days in last 14
    total_workout_days = db.execute("""
        SELECT COUNT(DISTINCT date) as c FROM workouts
        WHERE user_id=? AND date >= date('now','-14 days')
    """, (uid,)).fetchone()["c"]
    missed_days = 14 - total_workout_days

    # ── Training load balance ──────────────────────────────
    load7 = db.execute("""
        SELECT COALESCE(SUM(duration_min),0) as t FROM workouts
        WHERE user_id=? AND date >= ?
    """, (uid, d7)).fetchone()["t"] or 0
    load30 = db.execute("""
        SELECT COALESCE(SUM(duration_min),0) as t FROM workouts
        WHERE user_id=? AND date >= ?
    """, (uid, d30)).fetchone()["t"] or 0
    avg_week_load = round(load30 / 4) if load30 else 0
    load_balance = "оптимальная" if 80 <= load7 <= avg_week_load*1.3 else \
                   "слишком высокая" if load7 > avg_week_load*1.4 else \
                   "слишком низкая" if load7 < avg_week_load*0.5 and avg_week_load>0 else "нормальная"
    load_balance_pct = round(load7 / avg_week_load * 100) if avg_week_load > 0 else 0

    # ── Nutrition 30d avg ──────────────────────────────────
    nut_avg = db.execute("""
        SELECT ROUND(AVG(daily_prot),1) as avg_p, ROUND(AVG(daily_cal),0) as avg_c
        FROM (
          SELECT date, SUM(protein_g) as daily_prot, SUM(calories) as daily_cal
          FROM nutrition WHERE user_id=? AND date >= ? GROUP BY date
        )
    """, (uid, d30)).fetchone()
    avg_prot = float(nut_avg["avg_p"] or 0) if nut_avg else 0
    avg_cal  = float(nut_avg["avg_c"] or 0) if nut_avg else 0
    prot_gap_30 = max(0, prot_goal - round(avg_prot))

    # ── Recovery avg ──────────────────────────────────────
    hrv_avg_30 = db.execute("""
        SELECT ROUND(AVG(hrv),1) as h FROM health_metrics
        WHERE user_id=? AND date >= ? AND hrv IS NOT NULL
    """, (uid, d30)).fetchone()
    hrv_avg = float(hrv_avg_30["h"] or 0) if hrv_avg_30 else 0

    sleep_avg_30 = db.execute("""
        SELECT ROUND(AVG(sleep_hours),1) as s FROM health_metrics
        WHERE user_id=? AND date >= ? AND sleep_hours IS NOT NULL
    """, (uid, d30)).fetchone()
    sleep_avg = float(sleep_avg_30["s"] or 0) if sleep_avg_30 else 0

    # ── Performance Score (0–100) ──────────────────────────
    score = 50  # base

    # VO2max trend (30%): +1 per 0.5 improvement, max ±15
    vo2_pts = min(15, max(-15, round(vo2_trend * 6))) if vo2_trend else 0
    score += vo2_pts

    # Running efficiency (25%): positive change = +pts
    if run_eff_change is not None:
        eff_pts = min(12, max(-12, round(run_eff_change * 0.6)))
        score += eff_pts
    else:
        eff_pts = 0

    # Consistency (20%): avg workouts/week
    consist_pts = min(10, max(-5, round((avg_per_week - 3) * 3)))
    score += consist_pts

    # Recovery (15%): based on HRV avg
    hrv_pts = min(8, max(-5, round((hrv_avg - 40) / 5))) if hrv_avg else 0
    score += hrv_pts

    # Nutrition (10%): protein adequacy
    nut_pts = 5 if avg_prot >= prot_goal * 0.85 else 0 if avg_prot >= prot_goal * 0.6 else -3
    score += nut_pts

    perf_score = max(10, min(99, round(score)))

    # ── Performance trend (last 30 days, weekly points) ───
    perf_trend = []
    for w in range(4, -1, -1):
        wstart = (dt.today() - timedelta(days=(w+1)*7)).isoformat()
        wend   = (dt.today() - timedelta(days=w*7)).isoformat()
        w_cnt = db.execute("""
            SELECT COUNT(*) as c FROM workouts WHERE user_id=? AND date>=? AND date<?
        """, (uid, wstart, wend)).fetchone()["c"]
        w_hrv = db.execute("""
            SELECT AVG(hrv) as h FROM health_metrics WHERE user_id=? AND date>=? AND date<? AND hrv IS NOT NULL
        """, (uid, wstart, wend)).fetchone()["h"]
        # mini score for that week
        w_score = 50 + min(10, w_cnt*2) + (min(8, round((float(w_hrv)-40)/5)) if w_hrv else 0)
        perf_trend.append({"week": f"-{w*7}д", "score": max(10, min(99, round(w_score)))})

    # ── Status label ──────────────────────────────────────
    trend_delta = perf_trend[-1]["score"] - perf_trend[0]["score"] if len(perf_trend) >= 2 else 0
    if trend_delta >= 4:    status = "Форма растёт"
    elif trend_delta >= 1:  status = "Форма стабильна"
    elif trend_delta >= -2: status = "Форма стабильна"
    else:                   status = "Накоплена усталость"

    # ── Summary bullets ───────────────────────────────────
    summary = []
    if aerobic_improvement and aerobic_improvement > 3:
        summary.append("темп Z2 улучшился на "+str(aerobic_improvement)+" сек/км")
    if vo2_trend > 0:
        summary.append("VO₂max +"+str(vo2_trend)+" за 30 дней")
    elif vo2_trend < -0.5:
        summary.append("VO₂max снизился на "+str(abs(vo2_trend)))
    if hrv_avg >= 45:
        summary.append("HRV стабилен")
    elif hrv_avg > 0:
        summary.append("HRV ниже нормы — нужно больше отдыха")
    if avg_per_week >= 4:
        summary.append("нагрузка оптимальная")
    elif avg_per_week < 2:
        summary.append("мало тренировок — нужно добавить")
    if not summary:
        summary.append("синхронизируй Garmin для анализа")

    return {
        "score": perf_score,
        "status": status,
        "trend_delta": trend_delta,
        "summary": summary,
        "perf_trend": perf_trend,
        "vo2max": vo2max_now,
        "vo2max_trend": vo2_trend,
        "z2_pace": secs_to_pace(z2_pace_now),
        "z2_hr": z2_hr_now,
        "aerobic_improvement_sec": aerobic_improvement,
        "run_eff_change": run_eff_change,
        "avg_per_week": avg_per_week,
        "consec_weeks": consec_weeks,
        "missed_days_14": missed_days,
        "weeks_data": weeks_data,
        "load7": load7,
        "avg_week_load": avg_week_load,
        "load_balance": load_balance,
        "load_balance_pct": load_balance_pct,
        "avg_prot_30": round(avg_prot),
        "avg_cal_30": round(avg_cal),
        "prot_goal": prot_goal,
        "prot_gap_30": prot_gap_30,
        "hrv_avg_30": hrv_avg,
        "sleep_avg_30": sleep_avg,
        "score_breakdown": {
            "vo2max": vo2_pts,
            "efficiency": eff_pts,
            "consistency": consist_pts,
            "recovery": hrv_pts,
            "nutrition": nut_pts,
        }
    }
