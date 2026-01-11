import asyncio
import requests
import uuid
import os
import json
import base64
from datetime import datetime, timedelta
from urllib.parse import quote
from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse, JSONResponse

app = FastAPI()

# --- CONFIG ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

jobs = {}
user_limits = {}

def get_real_ip(request: Request):
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host

@app.get("/")
async def home(): return FileResponse("index.html")

# --- POLLINATIONS ENGINE (STABLE OPENAI MODEL) ---
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = get_real_ip(request)
    now = datetime.now()
    
    if user_ip not in user_limits or now > user_limits[user_ip]['reset']:
        user_limits[user_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[user_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "DAILY LIMIT REACHED (3/3). Try again tomorrow."}

    # Strict Instructions for English Lyrics
    system_p = (
        f"Write professional, romantic English song lyrics for: {prompt}. "
        "Theme: Romantic, traveling, soulful vibes. "
        "Strict Rule: Use ONLY English language. No other languages. "
        "Strict Length: Must be between 250 to 290 words. "
        "Do not include any intro or conversational text. Just the song lyrics."
    )
    
    api_url = f"https://text.pollinations.ai/{quote(system_p)}?model=openai&seed={uuid.uuid4().int % 1000}"
    
    try:
        res = await asyncio.to_thread(requests.get, api_url, timeout=25)
        lyrics = res.text
        
        # Error Detection: If AI summarizes or fails
        if not lyrics or len(lyrics.split()) < 150 or "can't" in lyrics.lower():
            return {"job_id": "error", "lyrics": "Error occurred please try again in sometime or use small lyrics."}
            
    except:
        return {"job_id": "error", "lyrics": "Error occurred please try again in sometime or use small lyrics."}

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "ip": user_ip, "topic": prompt, "status": "Pending"}
    return {"job_id": job_id, "lyrics": lyrics}

@app.post("/confirm-lyrics")
async def confirm(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    u_ip = get_real_ip(request)
    if job_id in jobs:
        user_limits[u_ip]['count'] += 1
        asyncio.create_task(music_worker(job_id, topic, final_lyrics, u_ip))
        return {"status": "started"}
    return JSONResponse({"status": "expired"}, 404)

async def music_worker(job_id, topic, lyrics, u_ip):
    try:
        payload = {"prompt": f"{topic} romantic traveling style english vocal", "lyrics": lyrics[:2000], "config": {"model": "sonic"}}
        headers = {'X-Forwarded-For': u_ip, 'User-Agent': "Mozilla/5.0", 'Origin': 'https://notegpt.io'}
        cookies = {'anonymous_user_id': str(uuid.uuid4()), 'is_accepted_terms': '1'}
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:4]}"
        req_data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}
        
        res = await asyncio.to_thread(requests.post, target, json=req_data, timeout=50)
        m_data = res.json()
        if m_data.get("code") == 100000:
            cid = m_data["data"]["conversation_id"]
            for _ in range(45):
                await asyncio.sleep(8)
                check = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies)
                s_data = check.json().get("data", {})
                if s_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": s_data.get("music_url")})
                    return
        jobs[job_id]["status"] = "Failed"
    except: jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def get_status(job_id: str): return jobs.get(job_id, {"status": "Expired"})
