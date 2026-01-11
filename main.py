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

# --- SERVER CONFIG ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

jobs = {}
user_limits = {}

def get_real_ip(request: Request):
    forwarded = request.headers.get("X-Forwarded-For")
    return forwarded.split(",")[0].strip() if forwarded else request.client.host

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
            payload = {"message": "Update", "content": encoded, "sha": sha} if sha else {"message": "Init", "content": encoded}
            requests.put(url, headers=headers, json=payload, timeout=10)
    except: return []

@app.get("/")
async def home(): return FileResponse("index.html")

# --- FULL WORKING ADMIN PANEL ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    data = sync_db("get")
    rows = "".join([f"<tr style='border-bottom:1px solid #333'><td style='padding:12px'>{i.get('ip')}</td><td style='padding:12px'>{i.get('topic')}</td><td style='padding:12px'>{i.get('time')}</td><td style='padding:12px;color:#3b82f6'>SUCCESS</td></tr>" for i in reversed(data)])
    return f"""
    <html><head><title>Admin Panel</title><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-[#030305] text-white p-10 font-sans">
        <h1 class="text-2xl font-bold mb-6 italic uppercase tracking-widest">Studio Terminal Logs</h1>
        <table class="w-full bg-white/5 rounded-2xl overflow-hidden text-sm">
            <thead class="bg-white/10 text-xs uppercase"><tr><th class="p-4">IP</th><th class="p-4">Topic</th><th class="p-4">Time</th><th class="p-4">Status</th></tr></thead>
            <tbody>{rows}</tbody>
        </table>
    </body></html>
    """

# --- LYRICS GENERATOR (STRICT ENGLISH) ---
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    u_ip = get_real_ip(request)
    now = datetime.now()
    
    if u_ip not in user_limits or now > user_limits[u_ip]['reset']:
        user_limits[u_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[u_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "DAILY LIMIT REACHED (3/3). Try again tomorrow."}

    # High-end Songwriting Instruction
    system_p = f"Professional songwriter. Write long English lyrics for: {prompt}. Traveling vibe, 260-290 words. No intro."
    api_url = f"https://text.pollinations.ai/{quote(system_p)}?model=openai&seed={uuid.uuid4().int % 999}"
    
    try:
        res = await asyncio.to_thread(requests.get, api_url, timeout=25)
        lyrics = res.text
        if not lyrics or len(lyrics.split()) < 150:
            return {"job_id": "error", "lyrics": "Error occurred please try again in sometime or use small lyrics."}
    except:
        return {"job_id": "error", "lyrics": "Error occurred please try again in sometime or use small lyrics."}

    jid = str(uuid.uuid4())
    jobs[jid] = {"lyrics": lyrics, "ip": u_ip, "topic": prompt, "status": "Ready"}
    
    db = sync_db("get")
    db.append({"ip": u_ip, "topic": prompt[:30], "time": now.strftime("%H:%M"), "status": "Success"})
    sync_db("put", db)
    
    return {"job_id": jid, "lyrics": lyrics}

@app.post("/confirm-lyrics")
async def confirm(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    u_ip = get_real_ip(request)
    if job_id in jobs:
        user_limits[u_ip]['count'] += 1
        asyncio.create_task(music_worker(job_id, topic, final_lyrics, u_ip))
        return {"status": "started"}
    return JSONResponse({"status": "expired"}, 404)

# --- STEALTH MUSIC WORKER (RE-ENGINEERED) ---
async def music_worker(jid, topic, lyrics, u_ip):
    try:
        # Masked payload - No mention of NoteGPT
        headers = {'X-Forwarded-For': u_ip, 'User-Agent': 'Mozilla/5.0'}
        payload = {"prompt": f"{topic} studio quality production", "lyrics": lyrics[:1900], "config": {"model": "sonic"}}
        
        # Using a direct internal routing strategy
        worker_endpoint = "https://google-worker.vercel.app/api/music" 
        req_body = {"target_url": "https://notegpt.io/api/v2/music/generate", "data": payload, "headers": headers}
        
        res = await asyncio.to_thread(requests.post, worker_endpoint, json=req_body, timeout=60)
        res_data = res.json()
        
        if res_data.get("code") == 100000:
            conv_id = res_data["data"]["conversation_id"]
            jobs[jid]["status"] = "Processing Master"
            
            for _ in range(45): # Enhanced Polling
                await asyncio.sleep(8)
                status_url = f"https://notegpt.io/api/v2/music/status?conversation_id={conv_id}"
                check = await asyncio.to_thread(requests.post, worker_endpoint, json={"target_url": status_url, "headers": headers})
                status_data = check.json().get("data", {})
                
                if status_data.get("status") == "success":
                    jobs[jid].update({"status": "Success", "audio": status_data.get("music_url")})
                    return
        jobs[jid]["status"] = "Failed (Server Busy)"
    except Exception:
        jobs[jid]["status"] = "System Error"

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "Disconnected"})
