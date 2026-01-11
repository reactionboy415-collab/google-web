import asyncio
import requests
import uuid
import os
from urllib.parse import quote
from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse
from duckduckgo_search import DDGS

app = FastAPI()
jobs = {}

class HybridEngine:
    async def start_via_tunnel(self, payload, user_ip):
        anon_id = str(uuid.uuid4())
        headers = {
            'X-Forwarded-For': user_ip,
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

# STEP 1: Sirf Lyrics Generate Karna
@app.post("/get-lyrics")
async def get_lyrics_api(prompt: str = Form(...)):
    with DDGS() as ddgs:
        # Instruction for transliteration (e.g., "Kaho na kaho" style)
        system_msg = (
            f"Write song lyrics about {prompt}. "
            "IMPORTANT: Use English alphabet but the pronunciation should be in the user's intended language (like Hindi/Tamil/Marathi). "
            "Example: 'Kaho na kaho' instead of 'Say it or not'. "
            "Structure: [Verse 1], [Chorus], [Verse 2]."
        )
        lyrics = ddgs.chat(system_msg, model='gpt-4o-mini')
        job_id = str(uuid.uuid4())
        jobs[job_id] = {"lyrics": lyrics, "status": "awaiting_confirmation"}
        return {"job_id": job_id, "lyrics": lyrics}

# STEP 2: User ki final lyrics se song banana
@app.post("/confirm-lyrics")
async def confirm_lyrics(job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...), request: Request = None):
    user_ip = request.headers.get("x-forwarded-for") or request.client.host
    if "," in user_ip: user_ip = user_ip.split(",")[0]
    
    jobs[job_id].update({"status": "Generating Music...", "progress": 30, "audio": None})
    asyncio.create_task(music_worker(job_id, topic, final_lyrics, user_ip))
    return {"status": "started"}

async def music_worker(job_id, topic, lyrics, user_ip):
    try:
        payload = {"prompt": f"{topic} high quality", "lyrics": lyrics[:2000]}
        res, cookies = await engine.start_via_tunnel(payload, user_ip)
        data = res.json()
        
        if data.get("code") == 100000:
            cid = data["data"]["conversation_id"]
            for i in range(1, 15):
                await asyncio.sleep(8)
                jobs[job_id]["progress"] = 30 + (i * 5)
                p_res = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies)
                p_data = p_res.json().get("data", {})
                
                if p_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "progress": 100, "audio": p_data.get("music_url")})
                    return
        jobs[job_id]["status"] = "Failed"
    except Exception:
        jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "not_found"})
