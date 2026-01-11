import asyncio
import requests
import uuid
import os
from datetime import datetime
from urllib.parse import quote
from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse, HTMLResponse

app = FastAPI()

# Memory Storage
activity_log = []
jobs = {}

class HybridEngine:
    async def start_via_tunnel(self, payload, user_ip):
        anon_id = str(uuid.uuid4())
        headers = {
            'X-Forwarded-For': user_ip,
            'X-Real-IP': user_ip,
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            'Origin': 'https://notegpt.io',
            'Referer': 'https://notegpt.io/ai-music-generator',
            'Accept': 'application/json'
        }
        cookies = {'anonymous_user_id': anon_id, 'is_accepted_terms': '1'}
        target_api = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:8]}"
        
        data = {
            "url": "https://notegpt.io/api/v2/music/generate",
            "payload": payload,
            "cookies": cookies,
            "headers": headers
        }
        # Using a higher timeout for the initial tunnel hit
        return await asyncio.to_thread(requests.post, target_api, json=data, timeout=60), cookies

engine = HybridEngine()

@app.get("/")
async def home():
    return FileResponse("index.html")

@app.get("/admin", response_class=HTMLResponse)
async def admin_panel():
    rows = "".join([f"<tr class='border-b border-gray-800'><td class='p-3 text-blue-400'>{a['ip']}</td><td class='p-3'>{a['topic']}</td><td class='p-3 text-xs'>{a['time']}</td><td class='p-3'><span class='px-2 py-1 rounded text-xs {'bg-green-900 text-green-200' if a['music'] == 'Success' else 'bg-red-900 text-red-200' if a['music'] == 'Failed' else 'bg-yellow-900 text-yellow-200'}'>{a['music']}</span></td></tr>" for a in reversed(activity_log)])
    return f"<html><head><script src='https://cdn.tailwindcss.com'></script></head><body class='bg-black text-white p-10'><h1 class='text-2xl font-bold mb-6 text-blue-500'>Activity Monitor</h1><table class='w-full text-left bg-gray-900 rounded-xl overflow-hidden'><thead><tr class='bg-gray-800 text-xs'> <th class='p-4'>IP</th><th class='p-4'>Topic</th><th class='p-4'>Time</th><th class='p-4'>Status</th></tr></thead><tbody>{rows}</tbody></table></body></html>"

@app.post("/get-lyrics")
async def get_lyrics_api(request: Request, prompt: str = Form(...)):
    user_ip = (request.headers.get("X-Forwarded-For") or request.client.host).split(",")[0].strip()
    
    # Advanced Pollinations Prompt for Transliteration
    system_p = f"Write transliterated lyrics (Hindi/Regional sounds in English letters) about {prompt}. Style: Catchy, [Verse 1], [Chorus]. Example: 'O mere dil ke chain' style."
    ly_url = f"https://text.pollinations.ai/{quote(system_p)}?model=openai&cache=false"
    
    res = await asyncio.to_thread(requests.get, ly_url)
    lyrics = res.text
    
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "status": "Awaiting Confirm", "ip": user_ip, "topic": prompt, "progress": 0}
    
    activity_log.append({"ip": user_ip, "topic": prompt[:30], "time": datetime.now().strftime("%H:%M:%S"), "music": "Reviewing"})
    return {"job_id": job_id, "lyrics": lyrics}

@app.post("/confirm-lyrics")
async def confirm_lyrics(request: Request, job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...)):
    user_ip = (request.headers.get("X-Forwarded-For") or request.client.host).split(",")[0].strip()
    
    if job_id in jobs:
        jobs[job_id].update({"status": "Connecting to API...", "progress": 20})
        asyncio.create_task(music_worker(job_id, topic, final_lyrics, user_ip))
        return {"status": "started"}
    return JSONResponse({"status": "error", "message": "Job not found"}, status_code=404)

async def music_worker(job_id, topic, lyrics, user_ip):
    try:
        payload = {
            "prompt": f"{topic} professional high quality studio master", 
            "lyrics": lyrics[:2000],
            "duration": 0,
            "config": {"model": "sonic"}
        }
        
        # 1. Start the generation
        response, cookies = await engine.start_via_tunnel(payload, user_ip)
        data = response.json()
        
        if data.get("code") == 100000:
            cid = data["data"]["conversation_id"]
            jobs[job_id].update({"status": "Rendering Audio...", "progress": 40})
            
            # 2. Polling Loop
            for i in range(1, 30): # 30 attempts x 8 seconds = 4 minutes max
                await asyncio.sleep(8)
                jobs[job_id]["progress"] = 40 + (i * 2)
                
                # We must use the same cookies returned from start_via_tunnel
                p_url = f"https://notegpt.io/api/v2/music/status?conversation_id={cid}"
                p_res = await asyncio.to_thread(requests.get, p_url, cookies=cookies, timeout=15)
                p_data = p_res.json().get("data", {})
                
                status = p_data.get("status")
                if status == "success":
                    audio_url = p_data.get("music_url")
                    jobs[job_id].update({"status": "Success", "progress": 100, "audio": audio_url})
                    # Update Log
                    for a in activity_log:
                        if a['ip'] == user_ip: a['music'] = "Success"
                    return
                elif status == "failed":
                    break

        jobs[job_id].update({"status": "Engine Busy. Try again.", "progress": 0})
        for a in activity_log:
            if a['ip'] == user_ip: a['music'] = "Failed"
            
    except Exception as e:
        print(f"Error: {e}")
        jobs[job_id].update({"status": "Connection Error", "progress": 0})

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "Job Expired", "progress": 0})
