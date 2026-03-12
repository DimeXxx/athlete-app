from fastapi import APIRouter, Depends
from pydantic import BaseModel
from typing import Optional
from database import get_db
from datetime import date

router = APIRouter()

class MealCreate(BaseModel):
    date: Optional[str] = None
    meal_name: str
    calories: int = 0
    protein_g: float = 0
    carbs_g: float = 0
    fat_g: float = 0

@router.get("/today")
def get_today_nutrition(db=Depends(get_db)):
    today = date.today().isoformat()
    meals = db.execute(
        "SELECT * FROM nutrition WHERE date = ? ORDER BY created_at",
        (today,)
    ).fetchall()
    totals = db.execute("""
        SELECT
            ROUND(SUM(calories)) as calories,
            ROUND(SUM(protein_g), 1) as protein_g,
            ROUND(SUM(carbs_g), 1) as carbs_g,
            ROUND(SUM(fat_g), 1) as fat_g
        FROM nutrition WHERE date = ?
    """, (today,)).fetchone()
    goals = {r["key"]: r["value"] for r in db.execute("SELECT key, value FROM goals").fetchall()}
    return {
        "meals": [dict(m) for m in meals],
        "totals": dict(totals),
        "goals": {
            "calories": int(goals.get("calories_target", 2300)),
            "protein_g": float(goals.get("protein_target", 150)),
            "carbs_g": float(goals.get("carbs_target", 230)),
            "fat_g": float(goals.get("fat_target", 75)),
        }
    }

@router.get("/history")
def get_nutrition_history(days: int = 30, db=Depends(get_db)):
    rows = db.execute("""
        SELECT date,
            ROUND(SUM(calories)) as calories,
            ROUND(SUM(protein_g), 1) as protein_g,
            ROUND(SUM(carbs_g), 1) as carbs_g,
            ROUND(SUM(fat_g), 1) as fat_g
        FROM nutrition
        WHERE date >= date('now', ? || ' days')
        GROUP BY date ORDER BY date DESC
    """, (f"-{days}",)).fetchall()
    return [dict(r) for r in rows]

@router.post("/")
def add_meal(meal: MealCreate, db=Depends(get_db)):
    meal_date = meal.date or date.today().isoformat()
    db.execute("""
        INSERT INTO nutrition (date, meal_name, calories, protein_g, carbs_g, fat_g)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (meal_date, meal.meal_name, meal.calories, meal.protein_g, meal.carbs_g, meal.fat_g))
    db.commit()
    id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    return {"id": id, "message": f"Added: {meal.meal_name}"}

@router.delete("/{meal_id}")
def delete_meal(meal_id: int, db=Depends(get_db)):
    db.execute("DELETE FROM nutrition WHERE id = ?", (meal_id,))
    db.commit()
    return {"message": "Deleted"}

@router.get("/goals")
def get_goals(db=Depends(get_db)):
    rows = db.execute("SELECT key, value FROM goals").fetchall()
    return {r["key"]: r["value"] for r in rows}

@router.post("/goals")
def update_goals(goals: dict, db=Depends(get_db)):
    for key, value in goals.items():
        db.execute("INSERT OR REPLACE INTO goals (key, value) VALUES (?, ?)", (key, str(value)))
    db.commit()
    return {"message": "Goals updated"}
