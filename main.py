import asyncio
import requests
import uuid
from urllib.parse import quote
from fastapi import FastAPI, Request, Form
from fastapi.responses import FileResponse

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

# STEP 1: Fast Lyrics using Pollinations with Advanced Prompt
@app.post("/get-lyrics")
async def get_lyrics_api(prompt: str = Form(...)):
    # Advanced System Prompt for Transliteration
    system_instruction = (
        "Write lyrics in transliterated style (English alphabets but Indian language sounds). "
        "Example: For Hindi, write 'Tum bin jiya jaye kaise' instead of 'How to live without you'. "
        f"Topic: {prompt}. Structure: [Verse 1], [Chorus], [Verse 2]."
    )
    
    # Fast fetch from Pollinations
    ly_url = f"https://text.pollinations.ai/{quote(system_instruction)}?model=openai&cache=false"
    res = await asyncio.to_thread(requests.get, ly_url)
    lyrics = res.text
    
    job_id = str(uuid.uuid4())
    jobs[job_id] = {"lyrics": lyrics, "status": "awaiting_confirmation"}
    return {"job_id": job_id, "lyrics": lyrics}

@app.post("/confirm-lyrics")
async def confirm_lyrics(job_id: str = Form(...), final_lyrics: str = Form(...), topic: str = Form(...), request: Request = None):
    user_ip = request.headers.get("x-forwarded-for") or request.client.host
    if "," in user_ip: user_ip = user_ip.split(",")[0]
    
    jobs[job_id].update({"status": "Generating Music...", "progress": 30, "audio": None})
    asyncio.create_task(music_worker(job_id, topic, final_lyrics, user_ip))
    return {"status": "started"}

async def music_worker(job_id, topic, lyrics, user_ip):
    try:
        payload = {"prompt": f"{topic} high quality studio", "lyrics": lyrics[:2000]}
        res, cookies = await engine.start_via_tunnel(payload, user_ip)
        data = res.json()
        
        if data.get("code") == 100000:
            cid = data["data"]["conversation_id"]
            for i in range(1, 20):
                await asyncio.sleep(7) # Slightly faster polling
                p_res = await asyncio.to_thread(requests.get, f"https://notegpt.io/api/v2/music/status?conversation_id={cid}", cookies=cookies)
                p_data = p_res.json().get("data", {})
                
                if p_data.get("status") == "success":
                    jobs[job_id].update({"status": "Success", "audio": p_data.get("music_url")})
                    return
        jobs[job_id]["status"] = "API Busy"
    except Exception:
        jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def get_status(job_id: str):
    return jobs.get(job_id, {"status": "not_found"})
