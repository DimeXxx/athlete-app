from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from contextlib import asynccontextmanager
import uvicorn, sqlite3, logging
from database import init_db, DB_PATH, get_db
from routers import workouts, nutrition, garmin, health, auth, ai, strava, strava
from routers.garmin import do_sync, load_garmin_creds, ensure_garmin_table, decode_pwd

logger = logging.getLogger(__name__)

def auto_sync_job():
    """Runs every 6 hours — syncs Garmin for all connected users"""
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        ensure_garmin_table(conn)
        rows = conn.execute("SELECT user_id, email, password_enc FROM garmin_accounts").fetchall()
        for row in rows:
            try:
                pwd = decode_pwd(row["password_enc"])
                synced, skipped, total = do_sync(row["user_id"], row["email"], pwd, 2, conn)
                logger.info(f"Auto-sync user {row['user_id']}: {synced} new workouts")
            except Exception as e:
                logger.error(f"Auto-sync failed for user {row['user_id']}: {e}")
        conn.close()
    except Exception as e:
        logger.error(f"Auto-sync job error: {e}")

@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    # Start background scheduler
    try:
        from apscheduler.schedulers.background import BackgroundScheduler
        scheduler = BackgroundScheduler()
        scheduler.add_job(auto_sync_job, 'interval', hours=6, id='garmin_sync')
        scheduler.start()
        logger.info("✅ Auto-sync scheduler started (every 6h)")
        yield
        scheduler.shutdown()
    except Exception as e:
        logger.error(f"Scheduler error: {e}")
        yield

app = FastAPI(title="Athlete App API", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router, prefix="/api/auth", tags=["auth"])
app.include_router(strava.router, prefix="/api/strava", tags=["strava"])
app.include_router(strava.router, prefix="/api/strava", tags=["strava"])

@app.get("/landing")
def landing_page():
    from fastapi.responses import FileResponse
    import os
    f = os.path.join(os.path.dirname(__file__), "../frontend/landing.html")
    return FileResponse(f)

@app.get("/admin")
def admin_panel():
    from fastapi.responses import FileResponse
    import os
    admin_file = os.path.join(os.path.dirname(__file__), "../frontend/admin.html")
    return FileResponse(admin_file)
app.include_router(ai.router, prefix="/api/ai", tags=["ai"])
app.include_router(workouts.router, prefix="/api/workouts", tags=["workouts"])
app.include_router(nutrition.router, prefix="/api/nutrition", tags=["nutrition"])
app.include_router(garmin.router, prefix="/api/garmin", tags=["garmin"])
app.include_router(health.router, prefix="/api/health", tags=["health"])

app.mount("/icons", StaticFiles(directory="../frontend/icons"), name="icons")

@app.get("/manifest.json")
async def manifest():
    return FileResponse("../frontend/manifest.json", media_type="application/manifest+json")

@app.get("/sw.js")
async def service_worker():
    return FileResponse("../frontend/sw.js", media_type="application/javascript",
                       headers={"Service-Worker-Allowed": "/"})

@app.get("/")
async def root():
    return FileResponse("../frontend/index.html")

@app.get("/api/ping")
async def ping():
    return {"status": "ok", "message": "Athlete App is running!"}

if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
