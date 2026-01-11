import asyncio
import requests
import uuid
import random
import os
from urllib.parse import quote
from fastapi import FastAPI, Request, Form, BackgroundTasks
from fastapi.responses import FileResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

# Store job progress
jobs = {}

class HybridEngine:
    async def start_via_tunnel(self, payload, user_ip):
        anon_id = str(uuid.uuid4())
        # Use the real user's IP in the headers
        headers = {
            'X-Forwarded-For': user_ip,
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
            'Origin': 'https://notegpt.io',
            'Referer': 'https://notegpt.io/ai-music-generator'
        }
        cookies = {'anonymous_user_id': anon_id, 'is_accepted_terms': '1'}
        target_api = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:8]}"
        
        data = {
            "url": "https://notegpt.io/api/v2/music/generate",
            "payload": payload,
            "cookies": cookies,
            "headers": headers
        }
        return await asyncio.to_thread(requests.post, target_api, json=data, timeout=45), cookies

    async def poll_locally(self, cid, cookies):
        url = f"https://notegpt.io/api/v2/music/status?conversation_id={cid}"
        return await asyncio.to_thread(requests.get, url, cookies=cookies, timeout=15)

engine = HybridEngine()

@app.get("/")
async def serve_home():
    return FileResponse("index.html")

@app.post("/generate")
async def handle_generate(request: Request, prompt: str = Form(...)):
    # Fetch User's Real IP
    user_ip = request.headers.get("x-forwarded-for") or request.client.host
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"status": "Starting...", "progress": 10, "audio": None}
    
    asyncio.create_task(music_worker(job_id, prompt, user_ip))
    return {"job_id": job_id}

async def music_worker(job_id, topic, user_ip):
    try:
        # Step 1: Lyrics
        jobs[job_id].update({"status": "Drafting lyrics...", "progress": 20})
        ly_p = f"Professional lyrics about {topic}. [Verse], [Chorus]."
        ly_res = requests.get(f"https://text.pollinations.ai/{quote(ly_p)}")
        lyrics = ly_res.text

        # Step 2: Start Studio
        jobs[job_id].update({"status": "Initializing Studio...", "progress": 40})
        payload = {"prompt": f"{topic} high quality", "lyrics": lyrics[:2000], "duration": 0, "config": {"model": "sonic"}}
        res, active_cookies = await engine.start_via_tunnel(payload, user_ip)
        data = res.json()

        if data.get("code") == 100000:
            cid = data["data"]["conversation_id"]
            # Step 3: Polling
            for i in range(1, 15):
                await asyncio.sleep(8)
                jobs[job_id]["progress"] = 40 + (i * 4)
                jobs[job_id]["status"] = f"Rendering track... {i*8}s"
                
                p_res = await engine.poll_locally(cid, active_cookies)
                p_data = p_res.json().get("data", {})
                
                if p_data.get("status") == "success":
                    jobs[job_id].update({"status": "Complete!", "progress": 100, "audio": p_data.get("music_url")})
                    return
        jobs[job_id]["status"] = "Studio Busy. Try again."
    except Exception as e:
        jobs[job_id]["status"] = f"Error: {str(e)}"

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "Not Found", "progress": 0})
