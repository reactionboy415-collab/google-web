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

# --- CONFIGURATION ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME") # Format: "username/repository"
FILE_PATH = "activity.json"

jobs = {}
user_limits = {} # Memory-based limits: {ip: {'count': 0, 'reset': time}}

# --- GITHUB HELPERS ---
def sync_github(action="get", data=None):
    if not GITHUB_TOKEN or not REPO_NAME: return [] if action == "get" else None
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    res = requests.get(url, headers=headers)
    sha = res.json().get('sha') if res.status_code == 200 else None
    
    if action == "get":
        if res.status_code == 200:
            return json.loads(base64.b64decode(res.json()['content']).decode())
        return []
    elif action == "put":
        content = base64.b64encode(json.dumps(data).encode()).decode()
        payload = {"message": "Sync Activity", "content": content, "sha": sha} if sha else {"message": "Init Log", "content": content}
        requests.put(url, headers=headers, json=payload)

class HybridEngine:
    async def start_via_tunnel(self, payload, user_ip):
        headers = {
            'X-Forwarded-For': user_ip,
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            'Origin': 'https://notegpt.io',
            'Referer': 'https://notegpt.io/'
        }
        cookies = {'anonymous_user_id': str(uuid.uuid4()), 'is_accepted_terms': '1'}
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:6]}"
        data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}
        return await asyncio.to_thread(requests.post, target, json=data, timeout=45), cookies

engine = HybridEngine()

@app.get("/")
async def index(): return FileResponse("index.html")

@app.get("/admin", response_class=HTMLResponse)
async def admin():
    log = sync_github("get")
    rows = "".join([f"<tr class='border-b border-gray-800'><td class='p-3 text-blue-400'>{a['ip']}</td><td class='p-3'>{a['topic']}</td><td class='p-3'>{a['time']}</td><td class='p-3'>{a['status']}</td></tr>" for a in reversed(log)])
    return f"""<html><head><script src='https://cdn.tailwindcss.com'></script></head><body class='bg-black text-white p-10'>
    <div class='flex justify-between mb-8'> <h1 class='text-2xl font-bold'>Live Activity (GitHub Cloud)</h1>
    <form action='/admin/clear' method='POST'><button class='bg-red-600 px-4 py-2 rounded'>Clear Logs</button></form></div>
    <table class='w-full bg-gray-900 rounded-lg overflow-hidden'><thead><tr class='bg-gray-800 text-left'>
    <th class='p-4'>User IP</th><th class='p-4'>Prompt</th><th class='p-4'>Timestamp</th><th class='p-4'>Result</th></tr></thead><tbody>{rows}</tbody></table></body></html>"""

@app.post("/admin/clear")
async def clear_logs():
    sync_github("put", [])
    return HTMLResponse("<script>alert('Logs Cleared'); window.location='/admin';</script>")

@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = (request.headers.get("X-Forwarded-For") or request.client.host).split(",")[0].strip()
    
    # 24H LIMIT LOGIC
    now = datetime.now()
    if user_ip not in user_limits or now > user_limits[user_ip]['reset']:
        user_limits[user_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[user_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "DAILY LIMIT REACHED (3/3). Try again in 24 hours."}

    # POLLINATIONS WITH STRICT 290 WORD LIMIT
    sys_prompt = f"Write professional transliterated lyrics about {prompt}. Use English letters for regional sounds. STRICTLY UNDER 280 WORDS. Format: [Verse 1], [Chorus]."
    res = await asyncio.to_thread(requests.get, f"https://text.pollinations.ai/{quote(sys_prompt)}?model=openai")
    
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": res.text, "ip": user_ip, "topic": prompt, "status": "Pending"}
    
    # Update GitHub Log
    logs = sync_github("get")
    logs.append({"ip": user_ip, "topic": prompt[:30], "time": now.strftime("%H:%M:%S"), "status": "Drafting"})
    sync_github("put", logs)
    
    return {"job_id": job_id, "lyrics": res.text}

@app.post("/confirm-lyrics")
async def confirm(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    user_ip = (request.headers.get("X-Forwarded-For") or request.client.host).split(",")[0].strip()
    if job_id in jobs:
        user_limits[user_ip]['count'] += 1
        asyncio.create_task(music_worker(job_id, topic, final_lyrics, user_ip))
        return {"status": "started"}
    return JSONResponse({"status": "error"}, 404)

async def music_worker(job_id, topic, lyrics, user_ip):
    try:
        payload = {"prompt": f"{topic} studio master", "lyrics": lyrics[:2000], "config": {"model": "sonic"}}
        res, cookies = await engine.start_via_tunnel(payload, user_ip)
        data = res.json()
        
        if data.get("code") == 100000:
            cid = data["data"]["conversation_id"]
            for _ in range(25):
                await asyncio.sleep(8)
                p_res = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies)
                p_data = p_res.json().get("data", {})
                if p_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": p_data.get("music_url")})
                    # Sync Success to GitHub
                    logs = sync_github("get")
                    for l in logs:
                        if l['ip'] == user_ip and l['status'] == "Drafting": l['status'] = "Success"
                    sync_github("put", logs)
                    return
        jobs[job_id]["status"] = "Failed"
    except: jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "Expired"})
