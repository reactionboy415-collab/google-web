import asyncio
import requests
import uuid
import os
import json
import base64
from datetime import datetime, timedelta
from urllib.parse import quote
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# --- CORS SETUP ---
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIG ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

jobs = {}
user_limits = {}

def get_client_ip(request: Request):
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    return x_forwarded_for.split(",")[0].strip() if x_forwarded_for else request.client.host

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
            requests.put(url, headers=headers, json={"message": "Sync", "content": encoded, "sha": sha} if sha else {"message": "Init", "content": encoded}, timeout=10)
    except: return []

@app.get("/")
async def home(): return {"status": "Online"}

@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = get_client_ip(request)
    now = datetime.now()
    if user_ip not in user_limits or now > user_limits[user_ip]['reset']:
        user_limits[user_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[user_ip]['count'] >= 5: # Daily limit set to 5
        return {"job_id": "error", "lyrics": "DAILY LIMIT REACHED."}

    # --- ULTRA STRICT SYSTEM PROMPT ---
    system_instruction = (
        "You are a professional Songwriter. "
        f"Prompt: {prompt}. "
        "STRICT RULES: 1. Use ONLY English Alphabet (Romanized). "
        "2. Structure: [Verse 1], [Chorus], [Verse 2], [Outro]. "
        "3. Length: Strictly between 150 to 180 words. "
        "4. Output ONLY the song. No chats, no disclaimers, no intros."
    )
    
    try:
        api_url = f"https://text.pollinations.ai/{quote(system_instruction)}?model=openai&seed={uuid.uuid4().int}"
        res = await asyncio.to_thread(requests.get, api_url, timeout=30)
        lyrics = res.text if res.status_code == 200 else "AI Error. Try again."
    except:
        lyrics = "Connection Timeout."

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "ip": user_ip, "topic": prompt, "status": "Pending"}
    history = sync_db("get"); history.append({"ip": user_ip, "topic": prompt[:30], "time": now.strftime("%H:%M"), "status": "Ready"}); sync_db("put", history)
    
    return {"job_id": job_id, "lyrics": lyrics}

@app.post("/confirm-lyrics")
async def confirm(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    user_ip = get_client_ip(request)
    if job_id in jobs:
        user_limits[user_ip]['count'] += 1
        asyncio.create_task(music_worker(job_id, topic, final_lyrics, user_ip))
        return {"status": "started"}
    return JSONResponse({"status": "expired"}, 404)

async def music_worker(job_id, topic, lyrics, user_ip):
    try:
        payload = {"prompt": f"Professional {topic}", "lyrics": lyrics[:2000], "duration": 0, "config": {"model": "sonic"}}
        headers = {'X-Forwarded-For': user_ip, 'User-Agent': "Mozilla/5.0", 'Origin': 'https://notegpt.io'}
        cookies = {'anonymous_user_id': str(uuid.uuid4()), 'is_accepted_terms': '1'}
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:4]}"
        
        res = await asyncio.to_thread(requests.post, target, json={"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}, timeout=45)
        m_data = res.json()
        
        if m_data.get("code") == 100000:
            cid = m_data["data"]["conversation_id"]
            for _ in range(60):
                await asyncio.sleep(8)
                check = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies, headers=headers)
                s_data = check.json().get("data", {})
                if s_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": s_data.get("music_url")})
                    return
        jobs[job_id]["status"] = "Failed"
    except: jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def check_status(job_id: str): return jobs.get(job_id, {"status": "Expired"})

@app.get("/xyz", response_class=HTMLResponse)
async def admin_panel():
    data = sync_db("get")
    rows = "".join([f"<tr class='border-b border-gray-800'><td class='p-3 text-blue-400 font-mono'>{a['ip']}</td><td class='p-3'>{a['topic']}</td><td class='p-3'>{a['time']}</td><td class='p-3'>{a['status']}</td></tr>" for a in reversed(data)])
    return f"<html><body style='background:#000;color:#fff;font-family:sans-serif;'><h1>LOGS</h1><table border='1'>{rows}</table></body></html>"

@app.post("/xyz/clear")
async def clear_logs(): sync_db("put", []); return "Cleared"
