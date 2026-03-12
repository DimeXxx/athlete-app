from fastapi import APIRouter, Depends, HTTPException
from database import get_db
from routers.auth import get_optional_user
from datetime import date, timedelta
import requests, json, os

router = APIRouter()

def get_uid(user):
    return user["id"] if user else 0  # 0 = guest, no real data

def compute_today_score(sleep_h, hrv, rhr, fatigue_pct):
    """Compute 0-100 readiness score"""
    score = 50  # baseline
    # Sleep: 8h = perfect
    if sleep_h:
        s = min(sleep_h / 8.0, 1.2)
        score += (s - 0.625) * 24
    # HRV: higher = better (normalize around 60ms)
    if hrv:
        score += min((hrv - 40) / 2, 15)
    # RHR: lower = better (normalize around 55)
    if rhr:
        score += min((60 - rhr) / 2, 12)
    # Fatigue: lower = better
    if fatigue_pct is not None:
        score -= fatigue_pct * 0.2
    return max(10, min(99, round(score)))

def score_label(score):
    if score >= 85: return "Отличная готовность", "green"
    if score >= 70: return "Хорошая готовность", "green"
    if score >= 55: return "Умеренная готовность", "yellow"
    if score >= 40: return "Нужен отдых", "orange"
    return "Восстановление", "red"

@router.get("/command-center")
def get_command_center(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    # --- Goals ---
    goals = {r["key"]: r["value"] for r in
             db.execute("SELECT key,value FROM goals WHERE user_id=?", (uid,)).fetchall()}

    # --- Today health metrics ---
    health_today = db.execute(
        "SELECT * FROM health_metrics WHERE user_id=? AND date=?", (uid, today)
    ).fetchone()
    health_today = dict(health_today) if health_today else {}

    # --- Yesterday health (for HRV/sleep from Garmin which comes next day) ---
    health_yest = db.execute(
        "SELECT * FROM health_metrics WHERE user_id=? AND date=?", (uid, yesterday)
    ).fetchone()
    health_yest = dict(health_yest) if health_yest else {}

    # Use most recent available values
    def pick(*vals):
        for v in vals:
            if v: return v
        return None

    sleep_h  = pick(health_today.get("sleep_hours"), health_yest.get("sleep_hours"))
    hrv      = pick(health_today.get("hrv"), health_yest.get("hrv"))
    rhr      = pick(health_today.get("resting_hr"), health_yest.get("resting_hr"))
    weight   = pick(health_today.get("weight_kg"), health_yest.get("weight_kg"))
    steps    = health_today.get("steps") or 0
    vo2max   = pick(health_today.get("vo2max"), health_yest.get("vo2max"))

    # --- Yesterday workout ---
    yest_workouts = db.execute("""
        SELECT type, distance_km, duration_min, calories, avg_hr
        FROM workouts WHERE user_id=? AND date=? ORDER BY id DESC LIMIT 1
    """, (uid, yesterday)).fetchone()
    yest_workout = dict(yest_workouts) if yest_workouts else None

    # --- Today nutrition ---
    nut_today = db.execute("""
        SELECT ROUND(SUM(calories)) as cal, ROUND(SUM(protein_g)) as prot,
               ROUND(SUM(carbs_g)) as carbs, ROUND(SUM(fat_g)) as fat,
               COUNT(*) as meals
        FROM nutrition WHERE user_id=? AND date=?
    """, (uid, today)).fetchone()
    nut = dict(nut_today) if nut_today else {}

    # --- Consecutive training days (fatigue) ---
    rows = db.execute("""
        SELECT DISTINCT date FROM workouts WHERE user_id=?
        AND date >= date('now','-14 days') AND date <= ?
        ORDER BY date DESC
    """, (uid, today)).fetchall()
    dates = [r["date"] for r in rows]
    consec = 0
    check = date.today()
    for _ in range(14):
        if check.isoformat() in dates:
            consec += 1
            check -= timedelta(days=1)
        else:
            break
    fatigue_pct = min(consec * 15, 80)

    # --- HRV trend ---
    hrv_trend = db.execute("""
        SELECT ROUND(AVG(hrv)) as avg_hrv FROM health_metrics
        WHERE user_id=? AND date >= date('now','-7 days') AND hrv IS NOT NULL
    """, (uid,)).fetchone()
    hrv_avg = hrv_trend["avg_hrv"] if hrv_trend else None

    # --- Today Score ---
    score = compute_today_score(sleep_h, hrv, rhr, fatigue_pct)
    label, color = score_label(score)

    # --- Nutrition gaps ---
    cal_goal  = int(goals.get("calories_target", 2300))
    prot_goal = int(goals.get("protein_target", 150))
    cal_eaten = nut.get("cal") or 0
    prot_eaten = nut.get("prot") or 0
    cal_gap  = cal_goal - cal_eaten
    prot_gap = prot_goal - prot_eaten

    # --- Risk alerts ---
    alerts = []
    if consec >= 3:
        alerts.append({"type":"warn","icon":"⚠️","title":f"Усталость: {consec} дней подряд",
                       "text":"Рекомендуется лёгкая тренировка или отдых сегодня."})
    if sleep_h and sleep_h < 6:
        alerts.append({"type":"warn","icon":"😴","title":"Мало сна",
                       "text":f"Только {sleep_h}ч сна. Интенсивная тренировка не рекомендуется."})
    if prot_gap > 50:
        alerts.append({"type":"info","icon":"🥩","title":f"Белка не хватает {prot_gap}г",
                       "text":"Добавь творог, яйца или куриную грудку."})
    if vo2max and float(vo2max) > 40:
        alerts.append({"type":"ok","icon":"📈","title":"Форма растёт",
                       "text":f"VO2max {vo2max} — ты на правильном треке к гонке."})

    # --- Training plan for today ---
    dow = date.today().weekday()  # 0=Mon
    plan_today = db.execute(
        "SELECT plan_json FROM user_plans WHERE user_id=? ORDER BY id DESC LIMIT 1", (uid,)
    ).fetchone()
    today_plan_item = None
    if plan_today:
        try:
            plan = json.loads(plan_today["plan_json"])
            # weekday: Mon=0..Sun=6, our plan: Sun=0..Sat=6
            plan_idx = (dow + 1) % 7
            if plan_idx < len(plan):
                today_plan_item = plan[plan_idx]
        except Exception:
            pass

    return {
        "score": score,
        "score_label": label,
        "score_color": color,
        "sleep_h": sleep_h,
        "hrv": hrv,
        "hrv_avg": hrv_avg,
        "rhr": rhr,
        "weight": weight,
        "steps": steps,
        "vo2max": vo2max,
        "fatigue_pct": fatigue_pct,
        "consec_days": consec,
        "yesterday_workout": yest_workout,
        "nutrition": nut,
        "cal_goal": cal_goal,
        "prot_goal": prot_goal,
        "cal_gap": cal_gap,
        "prot_gap": prot_gap,
        "alerts": alerts,
        "today_plan": today_plan_item,
        "goals": goals,
    }

@router.get("/weekly")
def get_weekly_analysis(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    api_key = os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key:
        raise HTTPException(400, "API key not configured")

    workouts = db.execute("""
        SELECT date, type, distance_km, duration_min, calories, avg_hr
        FROM workouts WHERE user_id=? AND date >= date('now','-7 days')
        ORDER BY date DESC
    """, (uid,)).fetchall()
    nutrition = db.execute("""
        SELECT date, SUM(calories) as cal, SUM(protein_g) as prot
        FROM nutrition WHERE user_id=? AND date >= date('now','-7 days')
        GROUP BY date ORDER BY date DESC
    """, (uid,)).fetchall()
    goals = {r["key"]: r["value"] for r in
             db.execute("SELECT key,value FROM goals WHERE user_id=?", (uid,)).fetchall()}

    prompt = f"""Ты персональный AI-тренер. Дай краткий анализ недели спортсмена на русском языке.

Тренировки за 7 дней:
{json.dumps([dict(w) for w in workouts], ensure_ascii=False)}

Питание за 7 дней:
{json.dumps([dict(n) for n in nutrition], ensure_ascii=False)}

Цели: {json.dumps(goals, ensure_ascii=False)}

Дай анализ в 3-4 предложениях: что хорошо, что улучшить, конкретный совет на следующую неделю. Будь конкретным и мотивирующим."""

    resp = requests.post("https://api.anthropic.com/v1/messages",
        headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json={"model": "claude-haiku-4-5-20251001", "max_tokens": 400,
              "messages": [{"role": "user", "content": prompt}]},
        timeout=30)
    resp.raise_for_status()
    data = resp.json()
    text = data["content"][0]["text"] if data.get("content") else "Нет данных"
    return {"analysis": text}


@router.get("/daily-tip")
def get_daily_tip(user=Depends(get_optional_user), db=Depends(get_db)):
    """Generate today's AI recommendation based on all available data"""
    uid = get_uid(user)
    api_key = os.environ.get("ANTHROPIC_API_KEY","")
    if not api_key:
        return {"tip": "Добавь API ключ для AI рекомендаций."}

    today = date.today().isoformat()
    yesterday = (date.today() - timedelta(days=1)).isoformat()

    health = db.execute(
        "SELECT * FROM health_metrics WHERE user_id=? AND date IN (?,?) ORDER BY date DESC LIMIT 1",
        (uid, today, yesterday)
    ).fetchone()
    h = dict(health) if health else {}

    nut = db.execute("""
        SELECT ROUND(SUM(calories)) as cal, ROUND(SUM(protein_g)) as prot
        FROM nutrition WHERE user_id=? AND date=?
    """, (uid, today)).fetchone()
    n = dict(nut) if nut else {}

    recent_workouts = db.execute("""
        SELECT date, type, distance_km, duration_min FROM workouts
        WHERE user_id=? AND date >= date('now','-5 days') ORDER BY date DESC LIMIT 5
    """, (uid,)).fetchall()

    goals = {r["key"]: r["value"] for r in
             db.execute("SELECT key,value FROM goals WHERE user_id=?", (uid,)).fetchall()}

    plan = db.execute(
        "SELECT plan_json, race_date, race_name FROM user_plans WHERE user_id=? ORDER BY id DESC LIMIT 1",
        (uid,)
    ).fetchone()
    race_info = f"Цель: {plan['race_name']}, дата: {plan['race_date']}" if plan else "Цель не указана"

    prompt = f"""Ты AI-тренер. Дай одну конкретную рекомендацию на сегодня. Только 1-2 предложения, без вступлений.

Данные спортсмена:
- Сон: {h.get('sleep_hours','?')}ч, HRV: {h.get('hrv','?')}, ЧСС покоя: {h.get('resting_hr','?')}
- Вес: {h.get('weight_kg','?')} кг
- Питание сегодня: {n.get('cal','0')} ккал, белок {n.get('prot','0')}г (цель: {goals.get('protein_target','150')}г)
- Последние тренировки: {json.dumps([dict(w) for w in recent_workouts], ensure_ascii=False)}
- {race_info}

Рекомендация (1-2 предложения, конкретно что делать сегодня):"""

    try:
        resp = requests.post("https://api.anthropic.com/v1/messages",
            headers={"x-api-key": api_key, "anthropic-version": "2023-06-01",
                     "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 150,
                  "messages": [{"role": "user", "content": prompt}]},
            timeout=20)
        resp.raise_for_status()
        data = resp.json()
        tip = data["content"][0]["text"] if data.get("content") else ""
        return {"tip": tip}
    except Exception as e:
        return {"tip": "Тренируйся по плану и следи за питанием."}
