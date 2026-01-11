import asyncio
import requests
import uuid
import os
import json
import base64
from datetime import datetime, timedelta
from urllib.parse import quote
from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

app = FastAPI()

# --- CONFIGURATION (Set in Render) ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

jobs = {}
user_limits = {}

# --- FETCH REAL USER IP (STRICT) ---
def get_user_ip(request: Request):
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.client.host

# --- GITHUB DATABASE ENGINE ---
def sync_db(action="get", new_data=None):
    if not GITHUB_TOKEN or not REPO_NAME: return []
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        sha = res.json().get('sha') if res.status_code == 200 else None
        if action == "get":
            return json.loads(base64.b64decode(res.json()['content']).decode()) if res.status_code == 200 else []
        elif action == "put":
            encoded = base64.b64encode(json.dumps(new_data).encode()).decode()
            payload = {"message": "Studio Update", "content": encoded, "sha": sha} if sha else {"message": "Init", "content": encoded}
            requests.put(url, headers=headers, json=payload, timeout=10)
    except: return []

@app.get("/")
async def serve_home(): return FileResponse("index.html")

# --- POLLINATIONS LYRICS ENGINE ---
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    u_ip = get_user_ip(request)
    
    # 3/3 Daily Limit
    now = datetime.now()
    if u_ip not in user_limits or now > user_limits[u_ip]['reset']:
        user_limits[u_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[u_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "DAILY LIMIT REACHED (3/3). Please try again tomorrow."}

    # Strict Songwriter Instruction
    system_instruction = (
        f"You are a professional songwriter. Write very long and detailed lyrics for: {prompt}. "
        "Strict Rule 1: Use real Marathi/Hindi words in English script. "
        "Strict Rule 2: Total length must be between 250 to 290 words. "
        "Strict Rule 3: Do not summarize. Do not chat. Only return lyrics."
    )
    
    # model=openai is the most stable active endpoint
    api_url = f"https://text.pollinations.ai/{quote(system_instruction)}?model=openai&seed={uuid.uuid4().int % 999}"
    
    try:
        res = await asyncio.to_thread(requests.get, api_url, timeout=30)
        lyrics = res.text
        
        # Check if AI failed or gave summary
        if not lyrics or len(lyrics) < 180 or "can't" in lyrics.lower():
            return {"job_id": "error", "lyrics": "Error occurred. Please try again in sometime or use small lyrics."}
            
    except:
        return {"job_id": "error", "lyrics": "Error occurred. Please try again in sometime or use small lyrics."}

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "ip": u_ip, "topic": prompt, "status": "Pending"}
    
    # Admin Log Sync
    db = sync_db("get")
    db.append({"ip": u_ip, "topic": prompt[:25], "time": now.strftime("%H:%M:%S"), "status": "Ready"})
    sync_db("put", db)
    
    return {"job_id": job_id, "lyrics": lyrics}

@app.post("/confirm-lyrics")
async def confirm(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    u_ip = get_user_ip(request)
    if job_id in jobs:
        user_limits[u_ip]['count'] += 1
        asyncio.create_task(music_worker(job_id, topic, final_lyrics, u_ip))
        return {"status": "started"}
    return JSONResponse({"status": "expired"}, 404)

async def music_worker(job_id, topic, lyrics, u_ip):
    try:
        payload = {"prompt": f"{topic} studio production", "lyrics": lyrics[:2000], "config": {"model": "sonic"}}
        # NoteGPT worker call with Spoofing
        headers = {'X-Forwarded-For': u_ip, 'User-Agent': "Mozilla/5.0", 'Origin': 'https://notegpt.io'}
        cookies = {'anonymous_user_id': str(uuid.uuid4()), 'is_accepted_terms': '1'}
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:4]}"
        data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}
        
        res = await asyncio.to_thread(requests.post, target, json=data, timeout=50)
        m_data = res.json()
        
        if m_data.get("code") == 100000:
            cid = m_data["data"]["conversation_id"]
            for _ in range(45): # Polling
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
