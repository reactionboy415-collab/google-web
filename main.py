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

def get_server_ip():
    try: return requests.get('https://api.ipify.org', timeout=5).text
    except: return "Unknown"

def get_client_ip(request: Request):
    # Sabse accurate user IP nikalne ka tarika
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        return x_forwarded_for.split(",")[0].strip()
    return request.client.host

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
            requests.put(url, headers=headers, json={"message": "Update", "content": encoded, "sha": sha} if sha else {"message": "Init", "content": encoded}, timeout=10)
    except: return []

@app.get("/")
async def home(): return {"status": "Mastering Engine Active"}

# --- 🎵 MASTERING WITH USER IP HEADERS ---
@app.post("/confirm-lyrics")
async def confirm(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    user_ip = get_client_ip(request)
    # Background task mein music generation start
    asyncio.create_task(music_worker(job_id, topic, final_lyrics, user_ip))
    return {"status": "started"}

async def music_worker(job_id, topic, lyrics, user_ip):
    try:
        jobs[job_id] = {"status": "Mastering...", "ip": user_ip}
        
        # User IP headers jo NoteGPT ko bypass karenge
        headers = {
            'X-Forwarded-For': user_ip,
            'X-Real-IP': user_ip,
            'Client-IP': user_ip,
            'True-Client-IP': user_ip,
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            'Origin': 'https://notegpt.io',
            'Referer': 'https://notegpt.io/ai-music-generator'
        }

        payload = {
            "prompt": f"Professional Studio, {topic}",
            "lyrics": lyrics[:2500],
            "duration": 0,
            "config": {"model": "sonic-v2"} 
        }

        # Proxy worker use kar rahe hain taaki direct Render IP block na ho
        target = f"https://google-worker.vercel.app/api/music"
        
        res = await asyncio.to_thread(requests.post, target, json={
            "url": "https://notegpt.io/api/v2/music/generate",
            "payload": payload,
            "headers": headers
        }, timeout=50)
        
        m_data = res.json()
        if m_data.get("code") == 100000:
            cid = m_data["data"]["conversation_id"]
            for _ in range(40): # Poll for 40 times
                await asyncio.sleep(8)
                check = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", headers=headers)
                s_data = check.json().get("data", {})
                
                if s_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": s_data.get("music_url")})
                    
                    # Log to GitHub
                    logs = sync_db("get")
                    logs.append({"ip": user_ip, "topic": topic[:30], "time": datetime.now().strftime("%H:%M:%S"), "status": "Success"})
                    sync_db("put", logs)
                    return
        jobs[job_id]["status"] = "Failed"
    except:
        jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def check_status(job_id: str): return jobs.get(job_id, {"status": "Processing"})

# --- 🔐 ADMIN PANEL (/XYZ) ---
@app.get("/xyz", response_class=HTMLResponse)
async def admin_panel():
    srv_ip = get_server_ip()
    data = sync_db("get")
    rows = "".join([f"<tr style='border-bottom:1px solid #222;'><td style='padding:12px;color:#3b82f6;'>{a['ip']}</td><td style='padding:12px;'>{a['topic']}</td><td style='padding:12px;'>{a.get('time','N/A')}</td><td style='padding:12px;'>{a['status']}</td></tr>" for a in reversed(data)])
    
    return f"""
    <html><head><script src="https://cdn.tailwindcss.com"></script></head>
    <body class="bg-black text-white p-8 font-sans">
        <div class="max-w-4xl mx-auto">
            <div class="bg-blue-900/10 p-8 rounded-3xl border border-blue-500/20 mb-10 flex justify-between items-center">
                <div><p class="text-[10px] text-blue-500 font-bold uppercase">Server IP</p><p class="text-3xl font-mono">{srv_ip}</p></div>
                <div class="text-right"><p class="text-[10px] text-gray-500 font-bold uppercase">Requests</p><p class="text-3xl font-mono">{len(data)}</p></div>
            </div>
            <div class="flex justify-between items-center mb-6">
                <h1 class="text-2xl font-black italic">STUDIO LOGS</h1>
                <form action="/xyz/clear" method="post"><button class="bg-red-600 px-6 py-2 rounded-xl text-xs font-bold uppercase">Clear All</button></form>
            </div>
            <div class="bg-gray-900/50 rounded-3xl border border-gray-800 overflow-hidden">
                <table class="w-full text-left text-sm">
                    <thead class="bg-gray-800 text-gray-500 text-[10px] uppercase font-bold">
                        <tr><th class="p-5">User IP</th><th class="p-5">Topic</th><th class="p-5">Time</th><th class="p-5">Status</th></tr>
                    </thead>
                    <tbody>{rows if rows else "<tr><td colspan='4' class='p-10 text-center'>No Records</td></tr>"}</tbody>
                </table>
            </div>
        </div>
    </body></html>
    """

@app.post("/xyz/clear")
async def clear_logs():
    sync_db("put", [])
    return HTMLResponse("<script>window.location='/xyz';</script>")
