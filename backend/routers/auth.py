from fastapi import APIRouter, Depends, HTTPException, Header
from pydantic import BaseModel, EmailStr
from typing import Optional
import sqlite3, hashlib, secrets, os, json
from database import get_db
from datetime import datetime

router = APIRouter()

# Simple token store (in DB)
def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode()).hexdigest()

def generate_token() -> str:
    return secrets.token_urlsafe(32)

class RegisterRequest(BaseModel):
    email: str
    password: str
    name: str = ""

class LoginRequest(BaseModel):
    email: str
    password: str

def get_current_user(authorization: Optional[str] = Header(None), db=Depends(get_db)):
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(401, "Not authenticated")
    token = authorization.replace("Bearer ", "")
    row = db.execute(
        "SELECT * FROM users WHERE token = ?", (token,)
    ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid token")
    return dict(row)

def get_optional_user(authorization: Optional[str] = Header(None), db=Depends(get_db)):
    """Returns user or None — for endpoints that work both ways"""
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.replace("Bearer ", "")
    row = db.execute("SELECT * FROM users WHERE token = ?", (token,)).fetchone()
    return dict(row) if row else None

@router.post("/register")
def register(req: RegisterRequest, db=Depends(get_db)):
    existing = db.execute("SELECT id FROM users WHERE email = ?", (req.email,)).fetchone()
    if existing:
        raise HTTPException(400, "Email already registered")
    
    token = generate_token()
    db.execute("""
        INSERT INTO users (email, name, password_hash, token, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (req.email, req.name or req.email.split("@")[0],
          hash_password(req.password), token, datetime.now().isoformat()))
    db.commit()
    
    user_id = db.execute("SELECT last_insert_rowid()").fetchone()[0]
    
    # Seed default goals for new user
    defaults = [
        ("calories_target", "2300"), ("protein_target", "150"),
        ("carbs_target", "230"), ("fat_target", "75"),
        ("steps_target", "10000"), ("sleep_target", "7.5"),
        ("weight_kg", "70"), ("race_date", "2026-04-26"),
        ("race_name", "Полумарафон 21 км"),
    ]
    for key, value in defaults:
        db.execute(
            "INSERT OR IGNORE INTO goals (user_id, key, value) VALUES (?, ?, ?)",
            (user_id, key, value)
        )
    db.commit()
    
    return {
        "token": token,
        "user": {"id": user_id, "email": req.email, "name": req.name or req.email.split("@")[0]}
    }

@router.post("/login")
def login(req: LoginRequest, db=Depends(get_db)):
    row = db.execute(
        "SELECT * FROM users WHERE email = ? AND password_hash = ?",
        (req.email, hash_password(req.password))
    ).fetchone()
    if not row:
        raise HTTPException(401, "Invalid email or password")
    
    # Refresh token on login
    token = generate_token()
    db.execute("UPDATE users SET token = ? WHERE id = ?", (token, row["id"]))
    db.commit()
    
    return {
        "token": token,
        "user": {"id": row["id"], "email": row["email"], "name": row["name"]}
    }

@router.get("/me")
def me(user=Depends(get_current_user)):
    return {"id": user["id"], "email": user["email"], "name": user["name"]}

@router.post("/logout")  
def logout(user=Depends(get_current_user), db=Depends(get_db)):
    db.execute("UPDATE users SET token = NULL WHERE id = ?", (user["id"],))
    db.commit()
    return {"message": "Logged out"}
