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

# --- ENTER YOUR GITHUB SETTINGS IN RENDER DASHBOARD ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

# In-memory fast cache
jobs = {}
user_limits = {}

# --- FAST GITHUB SYNC ENGINE ---
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
            payload = {"message": "Cloud Sync", "content": encoded, "sha": sha} if sha else {"message": "Init Log", "content": encoded}
            requests.put(url, headers=headers, json=payload, timeout=10)
    except Exception as e:
        print(f"Sync Error: {e}")
        return []

class MusicEngine:
    async def start_session(self, payload, user_ip):
        # Professional Headers for NoteGPT
        headers = {
            'X-Forwarded-For': user_ip,
            'User-Agent': "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15",
            'Origin': 'https://notegpt.io',
            'Referer': 'https://notegpt.io/ai-music-generator'
        }
        cookies = {'anonymous_user_id': str(uuid.uuid4()), 'is_accepted_terms': '1'}
        # Load balancing across random endpoints
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:4]}"
        data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}
        return await asyncio.to_thread(requests.post, target, json=data, timeout=45), cookies

engine = MusicEngine()

@app.get("/")
async def serve_home(): return FileResponse("index.html")

# --- HIGH-SPEED LYRICS (AI-HYPER PERPLEXITY) ---
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = (request.headers.get("X-Forwarded-For") or request.client.host).split(",")[0].strip()
    
    # Check 3/3 daily limit
    now = datetime.now()
    if user_ip not in user_limits or now > user_limits[user_ip]['reset']:
        user_limits[user_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[user_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "LIMIT EXCEEDED (3/3). Agle 24 ghante baad try karein."}

    # Strict Instruction for Zero Conversational Text
    query = (
        f"Write soulful romantic lyrics for a {prompt}. "
        "Strict Rule 1: Use real Marathi/Hindi words transliterated in English. "
        "Strict Rule 2: Output ONLY the lyrics. Do not say 'Here is your song' or anything else. "
        "Format: [Verse 1], [Chorus], [Verse 2]."
    )
    
    api_url = f"https://ai-hyper.vercel.app/api?q={quote(query)}"
    
    try:
        res = await asyncio.to_thread(requests.get, api_url, timeout=25)
        raw_data = res.json()
        lyrics = raw_data["results"]["answer"] if raw_data.get("ok") else "AI busy. Try again."
    except:
        lyrics = "Connection timed out. Retrying..."

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "ip": user_ip, "topic": prompt, "status": "Pending"}
    
    # Sync activity to GitHub
    history = sync_db("get")
    history.append({"ip": user_ip, "topic": prompt[:30], "time": now.strftime("%H:%M:%S"), "status": "Ready"})
    sync_db("put", history)
    
    return {"job_id": job_id, "lyrics": lyrics}

@app.post("/confirm-lyrics")
async def confirm(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    user_ip = (request.headers.get("X-Forwarded-For") or request.client.host).split(",")[0].strip()
    if job_id in jobs:
        user_limits[user_ip]['count'] += 1
        asyncio.create_task(music_worker(job_id, topic, final_lyrics, user_ip))
        return {"status": "started"}
    return JSONResponse({"status": "expired"}, 404)

async def music_worker(job_id, topic, lyrics, user_ip):
    try:
        payload = {
            "prompt": f"{topic} studio master recording",
            "lyrics": lyrics[:2000],
            "config": {"model": "sonic"}
        }
        res, cookies = await engine.start_session(payload, user_ip)
        data = res.json()
        
        if data.get("code") == 100000:
            cid = data["data"]["conversation_id"]
            # Fast Polling for 5 mins
            for _ in range(40):
                await asyncio.sleep(7)
                check = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies)
                status_data = check.json().get("data", {})
                
                if status_data.get("status") == "success":
                    audio_url = status_data.get("music_url")
                    jobs[job_id].update({"status": "Success", "audio": audio_url})
                    # Update GitHub
                    logs = sync_db("get")
                    for l in logs:
                        if l['ip'] == user_ip and l['status'] == "Ready": l['status'] = "Success"
                    sync_db("put", logs)
                    return
        jobs[job_id]["status"] = "Failed"
    except:
        jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def check_status(job_id: str):
    return jobs.get(job_id, {"status": "Session Expired"})

@app.get("/admin", response_class=HTMLResponse)
async def admin_logs():
    data = sync_db("get")
    rows = "".join([f"<tr class='border-b border-gray-800'><td class='p-3 text-blue-500 font-mono text-xs'>{a['ip']}</td><td class='p-3'>{a['topic']}</td><td class='p-3 text-xs'>{a['time']}</td><td class='p-3'><span class='text-[10px] px-2 py-1 rounded {'bg-green-900' if a['status']=='Success' else 'bg-blue-900'}'>{a['status']}</span></td></tr>" for a in reversed(data)])
    return f"""<html><head><script src='https://cdn.tailwindcss.com'></script></head><body class='bg-black text-white p-10'>
    <div class='max-w-4xl mx-auto flex justify-between mb-8'> <h1 class='text-2xl font-bold'>Studio Master Logs</h1>
    <form action='/admin/clear' method='POST'><button class='bg-red-600 px-4 py-2 rounded-lg font-bold'>Clear History</button></form></div>
    <table class='w-full bg-gray-900 rounded-xl overflow-hidden'><thead class='bg-gray-800 text-xs uppercase tracking-widest'><tr><th class='p-4'>IP Address</th><th class='p-4'>Prompt</th><th class='p-4'>Time</th><th class='p-4'>Status</th></tr></thead><tbody>{rows}</tbody></table></body></html>"""

@app.post("/admin/clear")
async def purge(): sync_db("put", []); return HTMLResponse("<script>window.location='/admin';</script>")
