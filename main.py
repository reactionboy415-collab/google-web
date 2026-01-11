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

# --- CONFIGURATION (Set in Render Dashboard) ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"

jobs = {}
user_limits = {}

# --- CORE LOGIC: FETCHING REAL USER IP (NOT SERVER) ---
def get_client_ip(request: Request):
    # Render, Cloudflare, aur Load Balancers 'X-Forwarded-For' header bhejte hain
    x_forwarded_for = request.headers.get("X-Forwarded-For")
    if x_forwarded_for:
        # Sabse pehla IP hamesha user ka asli IP hota hai
        return x_forwarded_for.split(",")[0].strip()
    return request.client.host

# --- GITHUB DATABASE SYNC ---
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
            payload = {"message": "Cloud Sync", "content": encoded, "sha": sha} if sha else {"message": "Init", "content": encoded}
            requests.put(url, headers=headers, json=payload, timeout=10)
    except: return []

@app.get("/")
async def index(): return FileResponse("index.html")

# --- POWERFUL LYRICS GENERATOR (AI-HYPER API) ---
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = get_client_ip(request) # <--- 101% REAL USER IP
    
    # 3/3 DAILY RATE LIMIT
    now = datetime.now()
    if user_ip not in user_limits or now > user_limits[user_ip]['reset']:
        user_limits[user_ip] = {'count': 0, 'reset': now + timedelta(days=1)}
    
    if user_limits[user_ip]['count'] >= 3:
        return {"job_id": "error", "lyrics": "DAILY LIMIT REACHED (3/3). Please try again tomorrow."}

    # Strict AI Prompt to avoid summaries
    query = (
        f"You are a professional songwriter. Write FULL long lyrics for: {prompt}. "
        "STRICT: ONLY return lyrics. Use real Marathi/Hindi words in English script. "
        "Length: 260-290 words. Do not summarize or explain."
    )
    
    api_url = f"https://ai-hyper.vercel.app/api?q={quote(query)}"
    
    try:
        res = await asyncio.to_thread(requests.get, api_url, timeout=30)
        lyrics = res.json()["results"]["answer"] if res.json().get("ok") else "AI is currently busy."
        if "can't produce" in lyrics.lower() or len(lyrics) < 100:
            lyrics = "[Verse 1]\nTujhya sathi saajana, man he bawre jhale...\n[Chorus]\nSajni mazi rani..."
    except:
        lyrics = "Connection error. Please retry."

    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "ip": user_ip, "topic": prompt, "status": "Pending"}
    
    # Sync Log to GitHub
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
        payload = {"prompt": f"{topic} studio production", "lyrics": lyrics[:2000], "config": {"model": "sonic"}}
        
        # NOTEGPT SPOOFING WITH USER IP
        headers = {
            'X-Forwarded-For': user_ip, 
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            'Origin': 'https://notegpt.io'
        }
        cookies = {'anonymous_user_id': str(uuid.uuid4()), 'is_accepted_terms': '1'}
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:4]}"
        data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}
        
        res = await asyncio.to_thread(requests.post, target, json=data, timeout=45)
        m_data = res.json()
        
        if m_data.get("code") == 100000:
            cid = m_data["data"]["conversation_id"]
            for _ in range(40): # Polling for status
                await asyncio.sleep(8)
                check = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies)
                status_data = check.json().get("data", {})
                if status_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": status_data.get("music_url")})
                    # Update GitHub
                    logs = sync_db("get")
                    for l in logs:
                        if l['ip'] == user_ip and l['status'] == "Ready": l['status'] = "Success"
                    sync_db("put", logs)
                    return
        jobs[job_id]["status"] = "Failed"
    except: jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def check_status(job_id: str):
    return jobs.get(job_id, {"status": "Expired"})

# --- PROFESSIONAL ADMIN MONITOR ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    data = sync_db("get")
    rows = "".join([f"<tr class='border-b border-gray-800'><td class='p-3 text-blue-400 font-mono text-xs'>{a['ip']}</td><td class='p-3'>{a['topic']}</td><td class='p-3 text-xs'>{a['time']}</td><td class='p-3'><span class='px-2 py-1 rounded text-[10px] {'bg-green-900 text-green-200' if a['status']=='Success' else 'bg-blue-900 text-blue-200'}'>{a['status']}</span></td></tr>" for a in reversed(data)])
    return f"""<html><head><script src='https://cdn.tailwindcss.com'></script></head><body class='bg-black text-white p-10'>
    <div class='max-w-4xl mx-auto'><div class='flex justify-between items-center mb-10'><h1 class='text-3xl font-black italic text-blue-600'>STUDIO MASTER LOGS</h1>
    <form action='/admin/clear' method='POST'><button class='bg-red-600 px-6 py-2 rounded-full font-bold text-xs hover:bg-red-700 transition'>PURGE ALL DATA</button></form></div>
    <div class='bg-gray-900 rounded-[2rem] overflow-hidden border border-gray-800 shadow-2xl'><table class='w-full text-left'><thead class='bg-gray-800/50 text-gray-400 text-[10px] uppercase tracking-widest'><tr>
    <th class='p-5'>Client IP</th><th class='p-5'>Topic</th><th class='p-5'>Timestamp</th><th class='p-5'>Final Status</th></tr></thead><tbody>{rows if rows else '<tr><td colspan="4" class="p-10 text-center text-gray-600 uppercase font-bold tracking-widest">No Studio Activity Recorded</td></tr>'}</tbody></table></div></div></body></html>"""

@app.post("/admin/clear")
async def clear_logs():
    sync_db("put", [])
    return HTMLResponse("<script>window.location='/admin';</script>")
