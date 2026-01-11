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

# --- INTERNAL CONFIG ---
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
            payload = {"message": "System Log", "content": encoded, "sha": sha} if sha else {"message": "Init", "content": encoded}
            requests.put(url, headers=headers, json=payload, timeout=10)
    except: return []

@app.get("/")
async def home(): return FileResponse("index.html")

@app.get("/admin", response_class=HTMLResponse)
async def admin_view():
    data = sync_db("get")
    rows = "".join([f"<tr style='border-bottom:1px solid #222'><td style='padding:12px'>{i.get('ip')}</td><td style='padding:12px'>{i.get('topic')}</td><td style='padding:12px'>{i.get('time')}</td><td style='padding:12px;color:#4ade80'>{i.get('status')}</td></tr>" for i in reversed(data)])
    return f"<html><body style='background:#000;color:#fff;font-family:sans-serif;padding:40px'><h2>STUDIO LOGS</h2><table style='width:100%;text-align:left;border-collapse:collapse'>{rows}</table></body></html>"

@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    u_ip = get_real_ip(request)
    now = datetime.now()
    
    if u_ip not in user_limits or now > user_limits[u_ip]['reset']:
        user_limits[u_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    if user_limits[u_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "DAILY LIMIT REACHED (3/3). Try again tomorrow."}

    # Strict English Travel Song Logic
    system_p = f"Write romantic English traveling song lyrics for: {prompt}. Length: 260-290 words. English ONLY. No talk, just lyrics."
    api_url = f"https://text.pollinations.ai/{quote(system_p)}?model=openai&seed={uuid.uuid4().int % 999}"
    
    try:
        res = await asyncio.to_thread(requests.get, api_url, timeout=25)
        lyrics = res.text
        if not lyrics or len(lyrics.split()) < 150:
            return {"job_id": "error", "lyrics": "Error occurred please try again in sometime or use small lyrics."}
    except:
        return {"job_id": "error", "lyrics": "Error occurred please try again in sometime or use small lyrics."}

    jid = str(uuid.uuid4())
    jobs[jid] = {"lyrics": lyrics, "ip": u_ip, "topic": prompt, "status": "Preparing"}
    
    db = sync_db("get")
    db.append({"ip": u_ip, "topic": prompt[:30], "time": now.strftime("%H:%M"), "status": "Ready"})
    sync_db("put", db)
    return {"job_id": jid, "lyrics": lyrics}

@app.post("/confirm-lyrics")
async def confirm(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    u_ip = get_real_ip(request)
    if job_id in jobs:
        user_limits[u_ip]['count'] += 1
        asyncio.create_task(process_music(job_id, topic, final_lyrics, u_ip))
        return {"status": "started"}
    return JSONResponse({"status": "error"}, 404)

async def process_music(jid, topic, lyrics, u_ip):
    # Hidden Internal API calls - No NoteGPT mentions
    try:
        # Step 1: Request Generation
        headers = {'X-Forwarded-For': u_ip, 'User-Agent': 'Mozilla/5.0'}
        payload = {"prompt": f"{topic} studio quality travel vibez", "lyrics": lyrics[:2000], "config": {"model": "sonic"}}
        
        # Using a masked worker URL to hide the source
        worker = f"https://google-worker.vercel.app/api/gen-{uuid.uuid4().hex[:6]}"
        req_data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "headers": headers}
        
        res = await asyncio.to_thread(requests.post, worker, json=req_data, timeout=50)
        data = res.json()
        
        if data.get("code") == 100000:
            cid = data["data"]["conversation_id"]
            jobs[jid]["status"] = "Generating..."
            
            for _ in range(45): # Stealth Polling
                await asyncio.sleep(8)
                status_req = {"url": f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", "headers": headers}
                check = await asyncio.to_thread(requests.post, worker.replace("gen-", "status-"), json=status_req)
                s_data = check.json().get("data", {})
                
                if s_data.get("status") == "success":
                    jobs[jid].update({"status": "Success", "audio": s_data.get("music_url")})
                    return
        jobs[jid]["status"] = "Server Busy (Retrying...)"
    except Exception as e:
        jobs[jid]["status"] = "System Error"

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "Disconnected"})
