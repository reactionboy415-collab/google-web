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

# --- CONFIG ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

jobs = {}
user_limits = {} # Memory cache for faster checking

# --- GITHUB DATABASE SYNC ---
def manage_db(action="get", new_data=None):
    if not GITHUB_TOKEN or not REPO_NAME: return []
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    # Get current file info
    res = requests.get(url, headers=headers)
    sha = res.json().get('sha') if res.status_code == 200 else None
    
    if action == "get":
        if res.status_code == 200:
            content = base64.b64decode(res.json()['content']).decode()
            return json.loads(content)
        return []
    
    elif action == "put":
        encoded = base64.b64encode(json.dumps(new_data).encode()).decode()
        payload = {"message": "Update Activity Log", "content": encoded, "sha": sha} if sha else {"message": "Create Log", "content": encoded}
        requests.put(url, headers=headers, json=payload)

class MusicEngine:
    async def start(self, payload, user_ip):
        headers = {'X-Forwarded-For': user_ip, 'User-Agent': 'Mozilla/5.0'}
        cookies = {'anonymous_user_id': str(uuid.uuid4())}
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:6]}"
        data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}
        return await asyncio.to_thread(requests.post, target, json=data, timeout=45), cookies

engine = MusicEngine()

@app.get("/")
async def home(): return FileResponse("index.html")

# --- ADMIN PANEL ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_view():
    data = manage_db("get")
    rows = "".join([f"<tr class='border-b border-gray-800'><td class='p-3 text-blue-400'>{a['ip']}</td><td class='p-3'>{a['topic']}</td><td class='p-3 text-xs'>{a['time']}</td><td class='p-3'>{a['status']}</td></tr>" for a in reversed(data)])
    return f"""<html><head><script src='https://cdn.tailwindcss.com'></script></head><body class='bg-black text-white p-10 font-sans'>
    <div class='flex justify-between mb-8'> <h1 class='text-2xl font-bold'>Studio Activity Monitor</h1>
    <form action='/admin/clear' method='POST'><button class='bg-red-600 px-4 py-2 rounded font-bold'>Clear Database</button></form></div>
    <table class='w-full bg-gray-900 rounded-xl overflow-hidden'><thead><tr class='bg-gray-800 text-left text-xs uppercase'>
    <th class='p-4'>User IP</th><th class='p-4'>Prompt</th><th class='p-4'>Time</th><th class='p-4'>Result</th></tr></thead><tbody>{rows if rows else '<tr><td colspan="4" class="p-10 text-center text-gray-600">No logs found.</td></tr>'}</tbody></table></body></html>"""

@app.post("/admin/clear")
async def clear_data():
    manage_db("put", [])
    return HTMLResponse("<script>alert('GitHub Database Cleared'); window.location='/admin';</script>")

@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = (request.headers.get("X-Forwarded-For") or request.client.host).split(",")[0].strip()
    
    # 3 SONG LIMIT LOGIC
    now = datetime.now()
    if user_ip not in user_limits or now > user_limits[user_ip]['reset']:
        user_limits[user_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[user_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "Limit Reached! You can only generate 3 songs per 24 hours."}

    # POLLINATIONS WITH STRICT WORD LIMIT
    sys_msg = f"Write lyrics about {prompt}. Transliterated style (English letters for regional sounds). STRICTLY UNDER 280 WORDS. Format: [Verse 1], [Chorus]."
    res = await asyncio.to_thread(requests.get, f"https://text.pollinations.ai/{quote(sys_msg)}?model=openai")
    
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": res.text, "ip": user_ip, "topic": prompt, "status": "Pending"}
    
    # Sync to GitHub
    db = manage_db("get")
    db.append({"ip": user_ip, "topic": prompt[:30], "time": now.strftime("%H:%M:%S"), "status": "Drafting"})
    manage_db("put", db)
    
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
        payload = {"prompt": f"{topic} studio music", "lyrics": lyrics[:2000], "config": {"model": "sonic"}}
        res, cookies = await engine.start(payload, user_ip)
        data = res.json()
        
        if data.get("code") == 100000:
            cid = data["data"]["conversation_id"]
            for _ in range(25):
                await asyncio.sleep(8)
                p_res = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies)
                p_data = p_res.json().get("data", {})
                if p_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": p_data.get("music_url")})
                    # Update GitHub
                    db = manage_db("get")
                    for entry in db:
                        if entry['ip'] == user_ip and entry['status'] == "Drafting": entry['status'] = "Success"
                    manage_db("put", db)
                    return
        jobs[job_id]["status"] = "Failed"
    except: jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "Expired"})
