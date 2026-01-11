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

def get_server_ip():
    try: return requests.get('https://api.ipify.org', timeout=5).text
    except: return "Unknown"

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
async def home(): return {"status": "Backend Active"}

@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = get_client_ip(request)
    now = datetime.now()
    
    # Simple limit check
    if user_ip not in user_limits or now > user_limits[user_ip]['reset']:
        user_limits[user_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    # Using a faster, more reliable prompt
    clean_prompt = f"Write a romantic song about {prompt} in Romanized Marathi (English alphabet). [Verse 1], [Chorus], [Verse 2], [Outro]. Length: 160 words strictly."
    api_url = f"https://text.pollinations.ai/{quote(clean_prompt)}?model=openai&seed={uuid.uuid4().int}"
    
    try:
        res = await asyncio.to_thread(requests.get, api_url, timeout=25)
        lyrics = res.text if res.status_code == 200 else "AI Busy. Try again."
    except:
        lyrics = "Connection Error."

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "ip": user_ip, "topic": prompt, "status": "Pending"}
    
    history = sync_db("get")
    history.append({"ip": user_ip, "topic": prompt[:30], "time": now.strftime("%H:%M:%S"), "status": "Ready"})
    sync_db("put", history)
    
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
                    logs = sync_db("get")
                    for l in logs:
                        if l['ip'] == user_ip and l['topic'][:30] == topic[:30]: l['status'] = "Success"
                    sync_db("put", logs); return
        jobs[job_id]["status"] = "Failed"
    except: jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def check_status(job_id: str): return jobs.get(job_id, {"status": "Expired"})

# --- 🔐 UPDATED ADMIN PANEL (/XYZ) ---
@app.get("/xyz", response_class=HTMLResponse)
async def admin_panel():
    srv_ip = get_server_ip()
    data = sync_db("get")
    rows = "".join([f"<tr style='border-bottom:1px solid #222;'><td style='padding:12px;color:#3b82f6;'>{a['ip']}</td><td style='padding:12px;'>{a['topic']}</td><td style='padding:12px;color:#666;'>{a['time']}</td><td style='padding:12px;'>{a['status']}</td></tr>" for a in reversed(data)])
    
    return f"""
    <html><head><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-black text-white p-8 font-sans">
        <div class="max-w-4xl mx-auto">
            <div class="flex justify-between items-center mb-10 bg-blue-900/10 p-6 rounded-3xl border border-blue-500/20">
                <div><p class="text-xs text-blue-500 font-bold uppercase">Server Public IP</p><p class="text-2xl font-mono">{srv_ip}</p></div>
                <div class="text-right"><p class="text-xs text-gray-500 font-bold uppercase">Total Requests</p><p class="text-2xl font-mono">{len(data)}</p></div>
            </div>
            <div class="flex justify-between items-center mb-6">
                <h1 class="text-2xl font-black italic">STUDIO ACTIVITY LOGS</h1>
                <form action="/xyz/clear" method="post"><button class="bg-red-600 hover:bg-red-700 text-white text-xs font-bold px-6 py-2 rounded-full transition">CLEAR ALL DATA</button></form>
            </div>
            <div class="bg-gray-900 rounded-3xl overflow-hidden border border-gray-800">
                <table class="w-full text-left">
                    <thead class="bg-gray-800 text-gray-400 text-[10px] uppercase">
                        <tr><th class="p-4">User IP</th><th class="p-4">Topic</th><th class="p-4">Time</th><th class="p-4">Status</th></tr>
                    </thead>
                    <tbody>{rows if rows else "<tr><td colspan='4' class='p-20 text-center text-gray-600'>NO ACTIVITY FOUND</td></tr>"}</tbody>
                </table>
            </div>
        </div>
    </body></html>
    """

@app.post("/xyz/clear")
async def clear_logs():
    sync_db("put", [])
    return HTMLResponse("<script>window.location='/xyz';</script>")
