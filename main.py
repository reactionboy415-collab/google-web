import asyncio
import requests
import uuid
import os
from datetime import datetime
from urllib.parse import quote
from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse, HTMLResponse

app = FastAPI()

# In-memory database to store all activities
activity_log = []
jobs = {}

class HybridEngine:
    async def start_via_tunnel(self, payload, user_ip):
        anon_id = str(uuid.uuid4())
        headers = {
            'X-Forwarded-For': user_ip,
            'X-Real-IP': user_ip,
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            'Origin': 'https://notegpt.io',
            'Referer': 'https://notegpt.io/ai-music-generator'
        }
        cookies = {'anonymous_user_id': anon_id, 'is_accepted_terms': '1'}
        target_api = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:8]}"
        data = {"url": "https://notegpt.io/api/v2/music/generate", "payload": payload, "cookies": cookies, "headers": headers}
        return await asyncio.to_thread(requests.post, target_api, json=data, timeout=45), cookies

engine = HybridEngine()

@app.get("/")
async def home():
    return FileResponse("index.html")

# --- ADMIN PAGE ROUTE ---
@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    rows = ""
    for act in reversed(activity_log): # Newest first
        rows += f"""
        <tr class="border-b border-gray-800 hover:bg-gray-900 transition">
            <td class="p-3 text-blue-400 font-mono text-sm">{act['ip']}</td>
            <td class="p-3 text-gray-300">{act['topic']}</td>
            <td class="p-3 text-xs text-gray-500">{act['time']}</td>
            <td class="p-3"><span class="px-2 py-1 rounded text-xs { 'bg-green-900 text-green-300' if act['music'] == 'Success' else 'bg-yellow-900 text-yellow-300' }">{act['music']}</span></td>
        </tr>
        """
    
    return f"""
    <html>
    <head><script src="https://cdn.tailwindcss.com"></script><title>Admin Dashboard</title></head>
    <body class="bg-black text-white p-10 font-sans">
        <div class="max-w-5xl mx-auto">
            <div class="flex justify-between items-center mb-10">
                <h1 class="text-3xl font-bold text-blue-500 underline decoration-blue-500/30">Admin Activity Log</h1>
                <button onclick="location.reload()" class="bg-blue-600 px-4 py-2 rounded text-sm font-bold">Refresh Data</button>
            </div>
            <div class="bg-gray-900/50 border border-gray-800 rounded-2xl overflow-hidden shadow-2xl">
                <table class="w-full text-left border-collapse">
                    <thead>
                        <tr class="bg-gray-800 text-gray-400 uppercase text-xs tracking-widest">
                            <th class="p-4">User IP Address</th>
                            <th class="p-4">Topic / Prompt</th>
                            <th class="p-4">Timestamp</th>
                            <th class="p-4">Music Status</th>
                        </tr>
                    </thead>
                    <tbody>{rows if rows else '<tr><td colspan="4" class="p-10 text-center text-gray-600">No activity recorded yet.</td></tr>'}</tbody>
                </table>
            </div>
        </div>
    </body>
    </html>
    """

@app.post("/get-lyrics")
async def get_lyrics_api(request: Request, prompt: str = Form(...)):
    # Track Lyric Request
    user_ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
    
    # Advanced Pollinations Prompt
    system_instruction = f"Write transliterated lyrics (English letters, regional sounds) about {prompt}. Structure: [Verse 1], [Chorus]."
    ly_url = f"https://text.pollinations.ai/{quote(system_instruction)}?model=openai&cache=false"
    res = await asyncio.to_thread(requests.get, ly_url)
    lyrics = res.text
    
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "status": "Awaiting Confirm", "ip": user_ip, "topic": prompt}
    
    # Log the start of activity
    activity_log.append({
        "ip": user_ip, "topic": prompt[:40], "time": datetime.now().strftime("%H:%M:%S"), "music": "Drafting"
    })
    
    return {"job_id": job_id, "lyrics": lyrics}

@app.post("/confirm-lyrics")
async def confirm_lyrics(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    user_ip = request.headers.get("X-Forwarded-For", request.client.host).split(",")[0].strip()
    jobs[job_id].update({"status": "Generating Music...", "progress": 30})
    
    asyncio.create_task(music_worker(job_id, topic, final_lyrics, user_ip))
    return {"status": "started"}

async def music_worker(job_id, topic, lyrics, user_ip):
    try:
        payload = {"prompt": f"{topic} high quality", "lyrics": lyrics[:2000]}
        res, cookies = await engine.start_via_tunnel(payload, user_ip)
        data = res.json()
        
        if data.get("code") == 100000:
            cid = data["data"]["conversation_id"]
            for _ in range(20):
                await asyncio.sleep(8)
                p_url = f"https://notegpt.io/api/v2/music/status?conversation_id={cid}"
                p_res = await asyncio.to_thread(requests.get, p_url, cookies=cookies)
                p_data = p_res.json().get("data", {})
                
                if p_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": p_data.get("music_url")})
                    # Update Log on Success
                    for act in activity_log:
                        if act['ip'] == user_ip and act['topic'] == topic[:40]:
                            act['music'] = "Success"
                    return
        # Update Log on Failure
        for act in activity_log:
            if act['ip'] == user_ip and act['topic'] == topic[:40]:
                act['music'] = "Failed"
    except Exception:
        pass

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "not_found", "progress": 0})
