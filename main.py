import asyncio
import requests
import uuid
import os
import json
import base64
import socket
from datetime import datetime, timedelta
from urllib.parse import quote
from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

app = FastAPI()

# --- CONFIGURATION (Ensure these are in your Environment Variables) ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

jobs = {}
user_limits = {}

# --- HELPER: GET SERVER IP (For Admin Display) ---
def get_server_ip():
    try:
        return requests.get('https://api.ipify.org', timeout=5).text
    except:
        return socket.gethostbyname(socket.gethostname())

# --- HELPER: GET REAL USER IP ---
def get_client_ip(request: Request):
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.client.host

# --- GITHUB DATABASE SYNC ---
def sync_db(action="get", new_data=None):
    if not GITHUB_TOKEN or not REPO_NAME: return []
    url = f"https://api.api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    try:
        res = requests.get(url, headers=headers, timeout=10)
        sha = res.json().get('sha') if res.status_code == 200 else None
        if action == "get":
            if res.status_code == 200:
                return json.loads(base64.b64decode(res.json()['content']).decode())
            return []
        elif action == "put":
            encoded = base64.b64encode(json.dumps(new_data).encode()).decode()
            payload = {"message": "Cloud Sync", "content": encoded, "sha": sha} if sha else {"message": "Init", "content": encoded}
            requests.put(url, headers=headers, json=payload, timeout=10)
    except: return []

@app.get("/")
async def index(): return FileResponse("index.html")

# --- STEP 1: DYNAMIC MULTI-LINGUAL LYRICS GENERATOR ---
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = get_client_ip(request)
    now = datetime.now()
    
    # Rate Limit: 3 per day
    if user_ip not in user_limits or now > user_limits[user_ip]['reset']:
        user_limits[user_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[user_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "DAILY LIMIT REACHED (3/3). Please try again tomorrow."}

    # THE ULTIMATE PROMPT: Handles any language but keeps script English
    system_instruction = (
        "You are a professional multi-lingual songwriter. "
        f"Context: Write a full original song based on this prompt: '{prompt}'. "
        "STRICT INSTRUCTIONS: "
        "1. Identify the language requested by the user (Hindi, Tamil, Spanish, etc.). "
        "2. If no language is specified, default to a mix of Hindi and English. "
        "3. SCRIPT: You MUST write the lyrics using only the ENGLISH ALPHABET (Romanized). "
        "Example: If Hindi, write 'O sanam, tere bina kya jina' instead of 'ओ सनम...'. "
        "4. FORMAT: Include [Verse 1], [Chorus], [Verse 2], [Bridge], [Outro]. "
        "5. LENGTH: Minimum 280-300 words. "
        "6. NO introductions, disclaimers, or copyright warnings. Just the raw lyrics."
    )
    
    # Use Pollinations AI (Unfiltered)
    api_url = f"https://text.pollinations.ai/{quote(system_instruction)}"
    
    try:
        res = await asyncio.to_thread(requests.get, api_url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
        lyrics = res.text if res.status_code == 200 else "AI Busy. Please retry."
        
        # Guard against empty or short responses
        if len(lyrics) < 100:
            lyrics = f"[Verse 1]\nSun mere humsafar, kya tujhe itni si bhi khabar...\n[Chorus]\nO jaana, {prompt} ki hai yeh kahani..."
    except:
        lyrics = "Connection error. Please retry."

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "ip": user_ip, "topic": prompt, "status": "Pending"}
    
    # Log to Admin Panel
    history = sync_db("get")
    history.append({"ip": user_ip, "topic": prompt[:30], "time": now.strftime("%H:%M:%S"), "status": "Ready"})
    sync_db("put", history)
    
    return {"job_id": job_id, "lyrics": lyrics}

# --- STEP 2: CONFIRM & MASTER MUSIC ---
@app.post("/confirm-lyrics")
async def confirm(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    user_ip = get_client_ip(request)
    if job_id in jobs:
        user_limits[user_ip]['count'] += 1
        asyncio.create_task(music_worker(job_id, topic, final_lyrics, user_ip))
        return {"status": "started"}
    return JSONResponse({"status": "expired"}, 404)

# --- STEP 3: NOTEGPT WORKER (SPOOFED IP) ---
async def music_worker(job_id, topic, lyrics, user_ip):
    try:
        # Strict Payload for NoteGPT
        payload = {
            "prompt": f"Professional {topic}", 
            "lyrics": lyrics[:2200], # Keep within limits
            "duration": 0,
            "config": {"model": "sonic"}
        }
        
        # Spoofing User IP in headers
        headers = {
            'X-Forwarded-For': user_ip, 
            'X-Real-IP': user_ip,
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            'Origin': 'https://notegpt.io',
            'Referer': 'https://notegpt.io/ai-music-generator'
        }
        
        cookies = {'anonymous_user_id': str(uuid.uuid4()), 'is_accepted_terms': '1'}
        
        # Send to NoteGPT (via your worker)
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:4]}"
        req_data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}
        
        res = await asyncio.to_thread(requests.post, target, json=req_data, timeout=45)
        m_data = res.json()
        
        if m_data.get("code") == 100000:
            cid = m_data["data"]["conversation_id"]
            # Polling for success
            for _ in range(50):
                await asyncio.sleep(8)
                check = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies, headers=headers)
                status_data = check.json().get("data", {})
                if status_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": status_data.get("music_url")})
                    # Update status in Admin logs
                    logs = sync_db("get")
                    for entry in logs:
                        if entry['ip'] == user_ip and entry['topic'][:30] == topic[:30]:
                            entry['status'] = "Success"
                    sync_db("put", logs)
                    return
        jobs[job_id]["status"] = "Failed"
    except:
        jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def check_status(job_id: str):
    return jobs.get(job_id, {"status": "Expired"})

# --- PROFESSIONAL ADMIN PANEL ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    srv_ip = get_server_ip()
    data = sync_db("get")
    rows = "".join([f"<tr class='border-b border-gray-800'><td class='p-3 text-blue-400 font-mono text-xs'>{a['ip']}</td><td class='p-3'>{a['topic']}</td><td class='p-3 text-[10px] text-gray-500'>{a['time']}</td><td class='p-3'><span class='px-2 py-1 rounded text-[10px] font-bold {'bg-green-900 text-green-200' if a['status']=='Success' else 'bg-blue-900 text-blue-200'}'>{a['status']}</span></td></tr>" for a in reversed(data)])
    
    return f"""<html><head><script src='https://cdn.tailwindcss.com'></script></head>
    <body class='bg-black text-white font-sans p-6'>
        <div class='max-w-4xl mx-auto'>
            <div class='bg-blue-600/10 border border-blue-500/20 p-6 rounded-3xl mb-8 flex justify-between items-center'>
                <div><p class='text-[10px] uppercase tracking-widest text-blue-500 font-bold mb-1'>Hosting Node IP</p><p class='text-2xl font-black font-mono'>{srv_ip}</p></div>
                <div class='text-right'><p class='text-[10px] uppercase tracking-widest text-gray-500 font-bold mb-1'>Total Requests</p><p class='text-2xl font-black font-mono text-white'>{len(data)}</p></div>
            </div>
            <div class='flex justify-between items-center mb-6'><h1 class='text-3xl font-black italic tracking-tighter'>STUDIO MASTER LOGS</h1>
            <form action='/admin/clear' method='POST'><button class='bg-red-600/20 text-red-500 border border-red-500/30 px-6 py-2 rounded-full font-bold text-[10px] uppercase hover:bg-red-600 hover:text-white transition'>Purge All Data</button></form></div>
            <div class='bg-gray-900/50 rounded-[2.5rem] overflow-hidden border border-gray-800 shadow-2xl'>
                <table class='w-full text-left'><thead class='bg-gray-800/50 text-gray-400 text-[10px] uppercase tracking-widest'><tr>
                <th class='p-5'>User/Client IP</th><th class='p-5'>Topic/Mood</th><th class='p-5'>Timestamp</th><th class='p-5'>Outcome</th></tr></thead>
                <tbody class='text-sm'>{rows if rows else '<tr><td colspan="4" class='p-10 text-center text-gray-600 uppercase font-bold tracking-widest'>No Session History</td></tr>'}</tbody></table>
            </div>
        </div>
    </body></html>"""

@app.post("/admin/clear")
async def clear_logs():
    sync_db("put", [])
    return HTMLResponse("<script>window.location='/admin';</script>")
