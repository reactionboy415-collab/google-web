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

# --- CONFIGURATION ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

jobs = {}
user_limits = {}

# --- FETCH SERVER IP (To show in Admin) ---
def get_server_ip():
    try:
        return requests.get('https://api.ipify.org', timeout=5).text
    except:
        return socket.gethostbyname(socket.gethostname())

# --- FETCH REAL USER IP ---
def get_client_ip(request: Request):
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.client.host

# --- GITHUB SYNC ---
def sync_db(action="get", new_data=None):
    if not GITHUB_TOKEN or not REPO_NAME: return []
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
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
            payload = {"message": "Sync", "content": encoded, "sha": sha} if sha else {"message": "Init", "content": encoded}
            requests.put(url, headers=headers, json=payload, timeout=10)
    except: return []

@app.get("/")
async def index(): return FileResponse("index.html")

@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = get_client_ip(request)
    now = datetime.now()
    
    if user_ip not in user_limits or now > user_limits[user_ip]['reset']:
        user_limits[user_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[user_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "DAILY LIMIT REACHED."}

    query = f"Write professional lyrics for: {prompt}. Min 280 words. [Verse 1], [Chorus] structure."
    api_url = f"https://ai-hyper.vercel.app/api?q={quote(query)}"
    
    try:
        res = await asyncio.to_thread(requests.get, api_url, timeout=30)
        lyrics = res.json()["results"]["answer"] if res.json().get("ok") else "AI Busy."
    except: lyrics = "Connection Error."

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
        # STRICT PAYLOAD AS DIRECTED
        payload = {
            "prompt": f"Professional {topic}", 
            "lyrics": lyrics, 
            "duration": 0,
            "config": {"model": "sonic"}
        }
        
        # USER IP SPOOFING HEADERS
        headers = {
            'X-Forwarded-For': user_ip,
            'X-Real-IP': user_ip,
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            'Origin': 'https://notegpt.io',
            'Referer': 'https://notegpt.io/ai-music-generator'
        }
        
        cookies = {'anonymous_user_id': str(uuid.uuid4()), 'is_accepted_terms': '1'}
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:4]}"
        data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}
        
        res = await asyncio.to_thread(requests.post, target, json=data, timeout=45)
        m_data = res.json()
        
        if m_data.get("code") == 100000:
            cid = m_data["data"]["conversation_id"]
            for _ in range(45):
                await asyncio.sleep(8)
                check = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies, headers=headers)
                s_data = check.json().get("data", {})
                if s_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": s_data.get("music_url")})
                    return
        jobs[job_id]["status"] = "Failed"
    except: jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def check_status(job_id: str):
    return jobs.get(job_id, {"status": "Expired"})

# --- ADMIN PANEL WITH SERVER IP & USER IP ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    server_ip = get_server_ip()
    data = sync_db("get")
    rows = "".join([f"<tr class='border-b border-gray-800'><td class='p-3 text-blue-400 font-mono'>{a['ip']}</td><td class='p-3'>{a['topic']}</td><td class='p-3 text-xs'>{a['time']}</td><td class='p-3'><span class='px-2 py-1 rounded text-[10px] {'bg-green-900' if a['status']=='Success' else 'bg-blue-900'}'>{a['status']}</span></td></tr>" for a in reversed(data)])
    
    return f"""<html><head><script src='https://cdn.tailwindcss.com'></script></head>
    <body class='bg-black text-white p-6'>
        <div class='max-w-4xl mx-auto'>
            <div class='bg-blue-900/20 border border-blue-500/30 p-4 rounded-2xl mb-6 flex justify-between items-center'>
                <span class='text-blue-400 font-bold uppercase text-xs tracking-widest'>Core Server IP:</span>
                <span class='font-mono text-xl text-white'>{server_ip}</span>
            </div>
            <div class='flex justify-between items-center mb-6'>
                <h1 class='text-2xl font-black italic'>STUDIO LOGS</h1>
                <form action='/admin/clear' method='POST'><button class='bg-red-600 px-4 py-2 rounded-xl text-xs font-bold'>PURGE</button></form>
            </div>
            <div class='bg-gray-900 rounded-3xl overflow-hidden border border-gray-800'>
                <table class='w-full text-left'><thead class='bg-gray-800 text-gray-400 text-[10px] uppercase'>
                <tr><th class='p-4'>User IP</th><th class='p-4'>Topic</th><th class='p-4'>Time</th><th class='p-4'>Status</th></tr></thead>
                <tbody>{rows if rows else '<tr><td colspan="4" class="p-10 text-center">No Data</td></tr>'}</tbody></table>
            </div>
        </div>
    </body></html>"""

@app.post("/admin/clear")
async def clear_logs():
    sync_db("put", [])
    return HTMLResponse("<script>window.location='/admin';</script>")
