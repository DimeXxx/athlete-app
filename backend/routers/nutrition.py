from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel
from typing import Optional
from database import get_db
from routers.auth import get_current_user, get_optional_user
from datetime import date

router = APIRouter()

class MealCreate(BaseModel):
    date: Optional[str] = None
    meal_name: str
    calories: int = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0

def get_uid(user):
    return user["id"] if user else 1  # fallback for demo

@router.get("/today")
def get_today_nutrition(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    today = date.today().isoformat()
    meals = db.execute(
        "SELECT * FROM nutrition WHERE user_id=? AND date=? ORDER BY created_at",
        (uid, today)
    ).fetchall()
    totals = db.execute("""
        SELECT ROUND(SUM(calories)) as calories,
               ROUND(SUM(protein_g),1) as protein_g,
               ROUND(SUM(carbs_g),1) as carbs_g,
               ROUND(SUM(fat_g),1) as fat_g
        FROM nutrition WHERE user_id=? AND date=?
    """, (uid, today)).fetchone()
    goals_rows = db.execute("SELECT key, value FROM goals WHERE user_id=?", (uid,)).fetchall()
    goals = {r["key"]: r["value"] for r in goals_rows}
    if not goals:
        goals = {"calories_target":"2300","protein_target":"150","carbs_target":"230","fat_target":"75"}
    return {
        "meals": [dict(m) for m in meals],
        "totals": dict(totals) if totals else {},
        "goals": {
            "calories": int(goals.get("calories_target", 2300)),
            "protein_g": float(goals.get("protein_target", 150)),
            "carbs_g": float(goals.get("carbs_target", 230)),
            "fat_g": float(goals.get("fat_target", 75)),
        }
    }

@router.post("/")
def add_meal(meal: MealCreate, user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    meal_date = meal.date or date.today().isoformat()
    db.execute("""
        INSERT INTO nutrition (user_id, date, meal_name, calories, protein_g, carbs_g, fat_g)
        VALUES (?, ?, ?, ?, ?, ?, ?)
    """, (uid, meal_date, meal.meal_name, meal.calories, meal.protein_g, meal.carbs_g, meal.fat_g))
    db.commit()
    id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": id, "message": f"Added: {meal.meal_name}"}

@router.delete("/{meal_id}")
def delete_meal(meal_id: int, user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    db.execute("DELETE FROM nutrition WHERE id=? AND user_id=?", (meal_id, uid))
    db.commit()
    return {"message": "Deleted"}

@router.get("/goals")
def get_goals(user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    rows = db.execute("SELECT key, value FROM goals WHERE user_id=?", (uid,)).fetchall()
    return {r["key"]: r["value"] for r in rows}

@router.post("/goals")
def update_goals(goals: dict, user=Depends(get_optional_user), db=Depends(get_db)):
    uid = get_uid(user)
    for key, value in goals.items():
        db.execute("INSERT OR REPLACE INTO goals (user_id, key, value) VALUES (?,?,?)",
                   (uid, key, str(value)))
    db.commit()
    return {"message": "Goals updated"}
