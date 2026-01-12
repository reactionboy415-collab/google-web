import asyncio
import uuid
import requests
import cloudscraper
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# =====================
# IN-MEMORY JOB STORE
# =====================
jobs = {}

# =====================
# HELPERS
# =====================
def get_client_ip(request: Request):
    xff = request.headers.get("X-Forwarded-For")
    return xff.split(",")[0].strip() if xff else request.client.host

# =====================
# 🎵 LYRICS API (YOUR /lyrics)
# =====================
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    ip = get_client_ip(request)

    try:
        r = requests.post(
            "https://ab-sunoai.vercel.app/api/lyrics",
            json={"prompt": prompt},
            timeout=30
        )
        lyrics = r.json().get("lyrics", "Lyrics failed")
    except Exception as e:
        return {"error": str(e)}

    job_id = str(uuid.uuid4())

    jobs[job_id] = {
        "prompt": prompt,
        "lyrics": lyrics,
        "status": "lyrics_ready",
        "audio": None,
        "created": datetime.utcnow(),
        "ip": ip
    }

    return {"job_id": job_id, "lyrics": lyrics}

# =====================
# 🎶 START MUSIC GEN
# =====================
@app.post("/confirm-lyrics")
async def confirm_lyrics(
    request: Request,
    job_id: str = Form(...),
    final_lyrics: str = Form(...),
    topic: str = Form(...)
):
    if job_id not in jobs:
        return JSONResponse({"error": "Invalid job"}, 404)

    jobs[job_id]["status"] = "generating_music"
    asyncio.create_task(music_worker(job_id, topic, final_lyrics, request))

    return {"status": "started", "job_id": job_id}

# =====================
# 🎧 MUSIC WORKER (300s SAFE)
# =====================
async def music_worker(job_id, topic, lyrics, request):
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
            jobs[job_id]["status"] = "failed"
            return

        cid = data["data"]["conversation_id"]

        # ⏳ Poll for up to 300 seconds
        start = datetime.utcnow()
        while (datetime.utcnow() - start).seconds < 300:
            await asyncio.sleep(8)

            s = scraper.get(
                f"https://notegpt.io/api/v2/music/status?conversation_id={cid}",
                headers=headers,
                cookies=cookies,
                timeout=30
            )

            info = s.json().get("data", {})
            if info.get("status") == "success":
                jobs[job_id]["audio"] = info.get("music_url")
                jobs[job_id]["status"] = "completed"
                return

            if info.get("status") == "failed":
                jobs[job_id]["status"] = "failed"
                return

        jobs[job_id]["status"] = "timeout"

    except:
        jobs[job_id]["status"] = "error"

# =====================
# 📊 STATUS API
# =====================
@app.get("/status/{job_id}")
async def check_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        return {"status": "expired"}

    if job["status"] == "completed":
        return {
            "status": "completed",
            "download_url": f"https://YOUR-DOMAIN-HERE/download/{job_id}"
        }

    return {"status": job["status"]}

# =====================
# ⬇️ DOWNLOAD (NO EMPTY FILE EVER)
# =====================
@app.get("/download/{job_id}")
async def download_song(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.get("audio"):
        return JSONResponse({"error": "File not ready"}, 404)

    scraper = cloudscraper.create_scraper()

    headers = {
        "User-Agent": "Mozilla/5.0 (Linux; Android 13)",
        "Referer": "https://notegpt.io/",
        "Origin": "https://notegpt.io"
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
