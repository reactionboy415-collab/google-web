import asyncio
import uuid
import requests
import cloudscraper
import os
import json
import base64
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------- CONFIG ----------------
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

# ---------------- STORE ----------------
jobs = {}

# ---------------- UTILS ----------------
def get_client_ip(request: Request):
    xff = request.headers.get("X-Forwarded-For")
    return xff.split(",")[0].strip() if xff else request.client.host

def get_server_ip():
    try:
        return requests.get("https://api.ipify.org", timeout=5).text
    except:
        return "Unknown"

def sync_db(action="get", new_data=None):
    if not GITHUB_TOKEN or not REPO_NAME:
        return []
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json"
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        sha = res.json().get("sha") if res.status_code == 200 else None
        if action == "get":
            return json.loads(base64.b64decode(res.json()["content"]).decode()) if res.status_code == 200 else []
        if action == "put":
            encoded = base64.b64encode(json.dumps(new_data).encode()).decode()
            requests.put(
                url,
                headers=headers,
                json={"message": "Update", "content": encoded, "sha": sha} if sha else {"message": "Init", "content": encoded},
                timeout=10
            )
    except:
        return []

@app.get("/")
async def home():
    return {"status": "Backend Live"}

# =======================
# 🎵 LYRICS (YOUR /lyrics)
# =======================
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    ip = get_client_ip(request)
    try:
        r = requests.post(
            "https://ab-sunoai.vercel.app/api/lyrics",
            json={"prompt": prompt},
            timeout=30
        )
        lyrics = r.json().get("lyrics", "Lyrics generation failed")
    except Exception as e:
        return {"error": str(e)}

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "prompt": prompt,
        "lyrics": lyrics,
        "status": "Pending",
        "audio": None,
        "ip": ip,
        "created": datetime.utcnow()
    }

    history = sync_db("get")
    history.append({
        "ip": ip,
        "topic": prompt[:30],
        "time": (datetime.now() + timedelta(hours=5, minutes=30)).strftime("%H:%M:%S"),
        "status": "Ready"
    })
    sync_db("put", history)

    return {"job_id": job_id, "lyrics": lyrics}

# =======================
# 🎶 CONFIRM → MUSIC
# =======================
@app.post("/confirm-lyrics")
async def confirm_lyrics(
    request: Request,
    job_id: str = Form(...),
    final_lyrics: str = Form(...),
    topic: str = Form(...)
):
    if job_id not in jobs:
        return JSONResponse({"status": "Expired"}, 404)

    jobs[job_id]["status"] = "Processing"
    asyncio.create_task(music_worker(job_id, topic, final_lyrics, request))
    return {"status": "started"}

# =======================
# 🎧 MUSIC WORKER (300s)
# =======================
async def music_worker(job_id: str, topic: str, lyrics: str, request: Request):
    scraper = cloudscraper.create_scraper()
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13)",
        "Origin": "https://notegpt.io",
        "Referer": "https://notegpt.io/ai-music-generator",
        "X-Requested-With": "XMLHttpRequest",
        "X-Forwarded-For": get_client_ip(request)
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

    try:
        gen = scraper.post(
            "https://notegpt.io/api/v2/music/generate",
            headers=headers,
            cookies=cookies,
            json=payload,
            timeout=60
        )
        data = gen.json()
        if data.get("code") != 100000:
            jobs[job_id]["status"] = "Failed"
            return

        cid = data["data"]["conversation_id"]

        start = datetime.utcnow()
        while (datetime.utcnow() - start).seconds < 300:
            await asyncio.sleep(8)
            st = scraper.get(
                f"https://notegpt.io/api/v2/music/status?conversation_id={cid}",
                headers=headers,
                cookies=cookies,
                timeout=30
            )
            info = st.json().get("data", {})
            if info.get("status") == "success":
                jobs[job_id]["audio"] = info.get("music_url")
                jobs[job_id]["status"] = "Success"

                logs = sync_db("get")
                for l in logs:
                    if l["ip"] == jobs[job_id]["ip"] and l["topic"][:30] == topic[:30]:
                        l["status"] = "Success"
                sync_db("put", logs)
                return

            if info.get("status") == "failed":
                jobs[job_id]["status"] = "Failed"
                return

        jobs[job_id]["status"] = "Failed"
    except:
        jobs[job_id]["status"] = "Error"

# =======================
# 📊 STATUS (FRONTEND MATCH)
# =======================
@app.get("/status/{job_id}")
async def check_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "Expired"}

    if job.get("status") == "Success":
        return {
            "status": "Success",
            "audio": f"https://google-autofill.kesug.com/download/{job_id}"
        }

    if job.get("status") in ["Failed", "Error"]:
        return {"status": "Failed"}

    return {"status": "Processing"}

# =======================
# ⬇️ DOWNLOAD (NO EMPTY MP3)
# =======================
@app.get("/download/{job_id}")
async def download_song(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.get("audio"):
        return JSONResponse({"error": "File not ready"}, 404)

    scraper = cloudscraper.create_scraper()
    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13)",
        "Referer": "https://notegpt.io/",
        "Origin": "https://notegpt.io",
        "Accept": "*/*"
    }

    def stream():
        with scraper.get(job["audio"], headers=headers, stream=True) as r:
            r.raise_for_status()
            for chunk in r.iter_content(8192):
                if chunk:
                    yield chunk

    return StreamingResponse(
        stream(),
        media_type="audio/mpeg",
        headers={
            "Content-Disposition": f'attachment; filename="songify_{job_id}.mp3"',
            "Cache-Control": "no-store"
        }
    )

# =======================
# 🔐 ADMIN PANEL (/xyz)
# =======================
@app.get("/xyz", response_class=HTMLResponse)
async def admin_panel():
    srv_ip = get_server_ip()
    data = sync_db("get")
    rows = "".join([
        f"<tr><td>{a['ip']}</td><td>{a['topic']}</td><td>{a['time']}</td><td>{a['status']}</td></tr>"
        for a in reversed(data)
    ])
    return f"""
    <html><body style="background:#000;color:#fff">
    <h2>Server IP: {srv_ip}</h2>
    <h3>Total Logs: {len(data)}</h3>
    <table border="1" cellpadding="8">{rows}</table>
    </body></html>
    """

@app.post("/xyz/clear")
async def clear_logs():
    sync_db("put", [])
    return HTMLResponse("<script>window.location='/xyz'</script>")
