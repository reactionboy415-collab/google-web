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
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

jobs = {}
user_limits = {} # Memory cache: {ip: {'count': 0, 'reset': time}}

# --- GITHUB CLOUD STORAGE LOGIC ---
def sync_db(action="get", new_data=None):
    if not GITHUB_TOKEN or not REPO_NAME: return []
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {"Authorization": f"token {GITHUB_TOKEN}", "Accept": "application/vnd.github.v3+json"}
    
    res = requests.get(url, headers=headers)
    sha = res.json().get('sha') if res.status_code == 200 else None
    
    if action == "get":
        if res.status_code == 200:
            try:
                content = base64.b64decode(res.json()['content']).decode()
                return json.loads(content)
            except: return []
        return []
    
    elif action == "put":
        encoded = base64.b64encode(json.dumps(new_data).encode()).decode()
        payload = {"message": "Sync Activity", "content": encoded, "sha": sha} if sha else {"message": "Init Log", "content": encoded}
        requests.put(url, headers=headers, json=payload)

class MusicEngine:
    async def start_tunnel(self, payload, user_ip):
        headers = {
            'X-Forwarded-For': user_ip,
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            'Origin': 'https://notegpt.io',
            'Referer': 'https://notegpt.io/ai-music-generator'
        }
        # Unique Session for every request
        cookies = {'anonymous_user_id': str(uuid.uuid4()), 'is_accepted_terms': '1'}
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:6]}"
        data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}
        return await asyncio.to_thread(requests.post, target, json=data, timeout=45), cookies

engine = MusicEngine()

@app.get("/")
async def home(): return FileResponse("index.html")

# --- ADMIN DASHBOARD ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    log = sync_db("get")
    rows = "".join([f"<tr class='border-b border-gray-800'><td class='p-3 text-blue-400 font-mono text-xs'>{a['ip']}</td><td class='p-3'>{a['topic']}</td><td class='p-3 text-xs text-gray-500'>{a['time']}</td><td class='p-3'><span class='px-2 py-1 rounded text-[10px] {'bg-green-900 text-green-200' if a['status']=='Success' else 'bg-yellow-900 text-yellow-200'}'>{a['status']}</span></td></tr>" for a in reversed(log)])
    return f"""<html><head><script src='https://cdn.tailwindcss.com'></script><title>Admin Monitor</title></head><body class='bg-black text-white p-10'>
    <div class='max-w-4xl mx-auto'><div class='flex justify-between items-center mb-8'><h1 class='text-2xl font-bold italic text-blue-500'>STUDIO LOGS</h1>
    <form action='/admin/clear' method='POST'><button class='bg-red-600 hover:bg-red-700 px-5 py-2 rounded-full text-xs font-bold'>CLEAR ALL HISTORY</button></form></div>
    <div class='bg-gray-900 rounded-2xl overflow-hidden border border-gray-800'><table class='w-full text-left'><thead class='bg-gray-800 text-gray-400 text-[10px] uppercase'><tr>
    <th class='p-4'>User IP</th><th class='p-4'>Topic</th><th class='p-4'>Time</th><th class='p-4'>Status</th></tr></thead><tbody>{rows if rows else '<tr><td colspan="4" class="p-10 text-center text-gray-600">No activity yet.</td></tr>'}</tbody></table></div></div></body></html>"""

@app.post("/admin/clear")
async def clear_history():
    sync_db("put", [])
    return HTMLResponse("<script>alert('GitHub Log Cleared'); window.location='/admin';</script>")

# --- LYRICS GENERATION (FIXED LLAMA ENGINE) ---
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = (request.headers.get("X-Forwarded-For") or request.client.host).split(",")[0].strip()
    
    # 3 SONG / 24H LIMIT
    now = datetime.now()
    if user_ip not in user_limits or now > user_limits[user_ip]['reset']:
        user_limits[user_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[user_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "DAILY LIMIT REACHED (3/3). Please try again after 24 hours."}

    # Better Prompt for Real Regional Language (No Gibberish)
    system_prompt = (
        f"Write romantic professional lyrics for a {prompt}. "
        "STRICT RULE: Use REAL Marathi/Hindi words written in English letters. "
        "Example: Instead of fake sounds, use words like 'Prem', 'Sajani', 'Tula', 'Aathvan'. "
        "STRICT LIMIT: Under 250 words. Format: [Verse 1], [Chorus], [Verse 2]."
    )
    
    # Using Llama model for better regional transliteration
    encoded_url = f"https://text.pollinations.ai/{quote(system_prompt)}?model=llama&cache=false&seed={uuid.uuid4().int % 1000}"
    
    try:
        res = await asyncio.to_thread(requests.get, encoded_url, timeout=20)
        lyrics = res.text if len(res.text) > 10 else "AI is busy. Please click again."
    except:
        lyrics = "Connection error. Please retry."

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "ip": user_ip, "topic": prompt, "status": "Pending"}
    
    # Save to GitHub
    db = sync_db("get")
    db.append({"ip": user_ip, "topic": prompt[:30], "time": now.strftime("%H:%M"), "status": "Drafting"})
    sync_db("put", db)
    
    return {"job_id": job_id, "lyrics": lyrics}

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
        payload = {
            "prompt": f"{topic} professional high quality studio record",
            "lyrics": lyrics[:2000],
            "config": {"model": "sonic"}
        }
        response, cookies = await engine.start_tunnel(payload, user_ip)
        data = response.json()
        
        if data.get("code") == 100000:
            cid = data["data"]["conversation_id"]
            for _ in range(30): # 4 minutes max polling
                await asyncio.sleep(8)
                p_res = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies)
                p_data = p_res.json().get("data", {})
                
                if p_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": p_data.get("music_url")})
                    # Update GitHub Log Status
                    db = sync_db("get")
                    for entry in db:
                        if entry['ip'] == user_ip and entry['status'] == "Drafting":
                            entry['status'] = "Success"
                    sync_db("put", db)
                    return
        jobs[job_id]["status"] = "Failed"
    except:
        jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "Session Expired"})
