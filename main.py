import asyncio
import uuid
import requests
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# ---------------- CORS ----------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- STORAGE ----------------
jobs = {}
logs = []

# ---------------- HELPERS ----------------
def get_client_ip(request: Request):
    xff = request.headers.get("X-Forwarded-For")
    return xff.split(",")[0] if xff else request.client.host

# =======================
# 🎵 LYRICS (YOUR /lyrics API)
# =======================
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    ip = get_client_ip(request)

    res = requests.post(
        "https://ab-sunoai.vercel.app/api/lyrics",
        json={"prompt": prompt},
        timeout=30
    )

    lyrics = res.json().get("lyrics", "Lyrics generation failed")

    job_id = uuid.uuid4().hex[:8]

    jobs[job_id] = {
        "status": "Pending",
        "lyrics": lyrics,
        "audio": None
    }

    logs.append({
        "ip": ip,
        "topic": prompt[:30],
        "time": (datetime.now() + timedelta(hours=5, minutes=30)).strftime("%H:%M:%S"),
        "status": "Lyrics Ready"
    })

    return {"job_id": job_id, "lyrics": lyrics}

# =======================
# 🎶 CONFIRM & MUSIC
# =======================
@app.post("/confirm-lyrics")
async def confirm(
    request: Request,
    job_id: str = Form(...),
    final_lyrics: str = Form(...),
    topic: str = Form(...)
):
    if job_id not in jobs:
        return JSONResponse({"status": "Expired"}, 404)

    asyncio.create_task(music_worker(job_id, topic, final_lyrics))
    return {"status": "started"}

# =======================
# 🎧 MUSIC WORKER (NOTE GPT – DIRECT URL)
# =======================
async def music_worker(job_id, topic, lyrics):
    try:
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://notegpt.io",
            "Referer": "https://notegpt.io/ai-music-generator"
        }
        cookies = {
            "anonymous_user_id": str(uuid.uuid4()),
            "is_accepted_terms": "1"
        }

        payload = {
            "prompt": f"{topic} studio quality",
            "lyrics": lyrics[:2000],
            "duration": 0,
            "config": {"model": "sonic"}
        }

        r = requests.post(
            "https://notegpt.io/api/v2/music/generate",
            headers=headers,
            cookies=cookies,
            json=payload,
            timeout=60
        )

        data = r.json()
        if data.get("code") != 100000:
            jobs[job_id]["status"] = "Failed"
            return

        cid = data["data"]["conversation_id"]

        # ⏳ poll up to 300s
        for _ in range(60):
            await asyncio.sleep(5)

            s = requests.get(
                "https://notegpt.io/api/v2/music/status",
                params={"conversation_id": cid},
                headers=headers,
                cookies=cookies,
                timeout=30
            )

            info = s.json().get("data", {})
            if info.get("status") == "success":
                jobs[job_id]["status"] = "Success"
                jobs[job_id]["audio"] = info.get("music_url")  # ✅ DIRECT CDN
                return

            if info.get("status") == "failed":
                jobs[job_id]["status"] = "Failed"
                return

        jobs[job_id]["status"] = "Failed"

    except:
        jobs[job_id]["status"] = "Error"

# =======================
# 📡 STATUS (FRONTEND COMPATIBLE)
# =======================
@app.get("/status/{job_id}")
async def status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "Expired"}

    if job["status"] == "Success":
        return {
            "status": "Success",
            "audio": job["audio"]   # ✅ DIRECT NOTE GPT URL
        }

    if job["status"] in ["Failed", "Error"]:
        return {"status": "Failed"}

    return {"status": "Processing"}

# =======================
# 🔐 ADMIN PANEL (UNCHANGED)
# =======================
@app.get("/xyz", response_class=HTMLResponse)
async def admin():
    rows = "".join([
        f"<tr><td>{l['ip']}</td><td>{l['topic']}</td><td>{l['time']}</td><td>{l['status']}</td></tr>"
        for l in reversed(logs)
    ])

    return f"""
    <html><body style="background:#000;color:#fff;font-family:sans-serif;padding:40px">
    <h1>ADMIN LOGS</h1>
    <table border="1" cellpadding="10">
        <tr><th>IP</th><th>Topic</th><th>Time</th><th>Status</th></tr>
        {rows or "<tr><td colspan=4>No Data</td></tr>"}
    </table>
    </body></html>
    """
