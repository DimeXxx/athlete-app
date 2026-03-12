from fastapi import APIRouter, Depends, HTTPException
from database import get_db
from routers.auth import get_optional_user
from datetime import date, timedelta
import httpx, os, json

router = APIRouter()

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")

async def call_claude(prompt: str) -> str:
    if not ANTHROPIC_API_KEY:
        return "AI-анализ недоступен: не задан ANTHROPIC_API_KEY"
    async with httpx.AsyncClient(timeout=30) as client:
        r = await client.post(
            "https://api.anthropic.com/v1/messages",
            headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"},
            json={"model": "claude-haiku-4-5-20251001", "max_tokens": 600,
                  "messages": [{"role": "user", "content": prompt}]}
        )
        data = r.json()
        return data["content"][0]["text"]

@router.get("/weekly")
async def weekly_analysis(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = user["id"] if user else 1
    today = date.today()
    week_ago = today - timedelta(days=7)

    workouts = db.execute("""
        SELECT type, date, distance_km, duration_min, avg_hr
        FROM workouts WHERE user_id=? AND date >= ?
        ORDER BY date DESC
    """, (uid, week_ago.isoformat())).fetchall()

    nutrition = db.execute("""
        SELECT ROUND(SUM(calories)) as cal, ROUND(SUM(protein_g),1) as prot,
               COUNT(DISTINCT date) as days
        FROM nutrition WHERE user_id=? AND date >= ?
    """, (uid, week_ago.isoformat())).fetchone()

    goals = db.execute("SELECT key, value FROM goals WHERE user_id=?", (uid,)).fetchall()
    goals_dict = {r["key"]: r["value"] for r in goals}

    race_date = goals_dict.get("race_date", "2026-04-26")
    days_to_race = (date.fromisoformat(race_date) - today).days

    workouts_text = "\n".join([
        f"- {w['date']}: {w['type']}, {w['distance_km'] or 0}км, {w['duration_min'] or 0}мин, пульс {w['avg_hr'] or '—'}"
        for w in workouts
    ]) or "Тренировок за неделю нет"

    nut = dict(nutrition) if nutrition else {}

    prompt = f"""Ты персональный тренер и диетолог. Проанализируй данные спортсмена за последние 7 дней и дай конкретные советы на русском языке.

ТРЕНИРОВКИ ЗА НЕДЕЛЮ:
{workouts_text}

ПИТАНИЕ ЗА НЕДЕЛЮ:
Калории: {nut.get('cal', 0)} ккал суммарно ({round((nut.get('cal') or 0)/max(nut.get('days',1),1))} ккал/день)
Белок: {nut.get('prot', 0)}г суммарно
Дней с записями питания: {nut.get('days', 0)} из 7

ЦЕЛИ:
Дней до гонки: {days_to_race}
Цель калорий/день: {goals_dict.get('calories_target', 2300)} ккал
Цель белка/день: {goals_dict.get('protein_target', 150)}г

Дай анализ в таком формате (используй эмодзи):
1. 💪 Что хорошо (2-3 пункта)
2. ⚠️ Что улучшить (2-3 конкретных совета)
3. 🎯 План на следующую неделю (3 конкретных действия)

Будь конкретным, используй цифры из данных. Максимум 300 слов."""

    try:
        analysis = await call_claude(prompt)
        return {"analysis": analysis, "period": f"{week_ago} — {today}", "workouts_count": len(workouts)}
    except Exception as e:
        raise HTTPException(500, str(e))

@router.get("/workout/{workout_id}")
async def analyze_workout(workout_id: int, user=Depends(get_optional_user), db=Depends(get_db)):
    uid = user["id"] if user else 1
    w = db.execute("SELECT * FROM workouts WHERE id=? AND user_id=?", (workout_id, uid)).fetchone()
    if not w:
        raise HTTPException(404, "Workout not found")
    w = dict(w)

    # get last 5 same type for comparison
    prev = db.execute("""
        SELECT AVG(distance_km) as avg_dist, AVG(duration_min) as avg_dur, AVG(avg_hr) as avg_hr
        FROM workouts WHERE user_id=? AND type=? AND id!=? AND distance_km > 0
        LIMIT 5
    """, (uid, w["type"], workout_id)).fetchone()
    p = dict(prev) if prev else {}

    pace = round(w["duration_min"] / w["distance_km"], 1) if w.get("distance_km") and w["distance_km"] > 0 else None

    prompt = f"""Коротко проанализируй тренировку спортсмена на русском языке (максимум 150 слов).

ТРЕНИРОВКА:
Тип: {w['type']}
Дата: {w['date']}
Дистанция: {w.get('distance_km', 0)} км
Время: {w.get('duration_min', 0)} мин
{"Темп: " + str(pace) + " мин/км" if pace else ""}
Средний пульс: {w.get('avg_hr', '—')} уд/мин

СРЕДНЕЕ ПО ПРЕДЫДУЩИМ {w['type']} ТРЕНИРОВКАМ:
Дистанция: {round(p.get('avg_dist') or 0, 1)} км
Время: {round(p.get('avg_dur') or 0, 1)} мин
Пульс: {round(p.get('avg_hr') or 0)} уд/мин

Дай: 1 похвалу, 1 наблюдение, 1 совет. Используй эмодзи."""

    try:
        analysis = await call_claude(prompt)
        return {"analysis": analysis, "workout": w}
    except Exception as e:
        raise HTTPException(500, str(e))
