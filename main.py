import asyncio
import requests
import uuid
import os
import json
import base64
from datetime import datetime, timedelta
from fastapi import FastAPI, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- CONFIG ---
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
REPO_NAME = os.getenv("REPO_NAME")
FILE_PATH = "activity.json"
HF_TOKEN = os.getenv("HF")

jobs = {}

def get_server_ip():
    try:
        return requests.get("https://api.ipify.org", timeout=5).text
    except:
        return "Unknown"

def get_client_ip(request: Request):
    xff = request.headers.get("X-Forwarded-For")
    return xff.split(",")[0].strip() if xff else request.client.host

def sync_db(action="get", new_data=None):
    if not GITHUB_TOKEN or not REPO_NAME:
        return []
    url = f"https://api.github.com/repos/{REPO_NAME}/contents/{FILE_PATH}"
    headers = {
        "Authorization": f"token {GITHUB_TOKEN}",
        "Accept": "application/vnd.github.v3+json",
    }
    try:
        res = requests.get(url, headers=headers, timeout=10)
        sha = res.json().get("sha") if res.status_code == 200 else None

        if action == "get":
            if res.status_code == 200:
                return json.loads(base64.b64decode(res.json()["content"]).decode())
            return []

        elif action == "put":
            encoded = base64.b64encode(json.dumps(new_data).encode()).decode()
            payload = {"message": "Update", "content": encoded}
            if sha:
                payload["sha"] = sha
            requests.put(url, headers=headers, json=payload, timeout=10)

    except:
        return []

@app.get("/")
async def home():
    return {"status": "Backend Live (HF Router Fixed)"}

# ======================================================
# 🎵 LYRICS GENERATION (ONLY PROMPT UPDATED)
# ======================================================
@app.post("/get-lyrics")
async def get_lyrics(request: Request, prompt: str = Form(...)):
    user_ip = get_client_ip(request)

    API_URL = "https://router.huggingface.co/v1/chat/completions"
    MODEL_ID = "meta-llama/Meta-Llama-3-8B-Instruct"

    headers = {
        "Authorization": f"Bearer {HF_TOKEN}",
        "Content-Type": "application/json",
    }

    payload = {
        "model": MODEL_ID,
        "messages": [
            {
                "role": "system",
                "content": "You are a professional songwriter."
            },
            {
                "role": "user",
                "content": (
                    f"User prompt/topic: {prompt}\n\n"
                    f"Instructions:\n"
                    f"- Detect the language from the user prompt.\n"
                    f"- Write the song in the SAME language.\n"
                    f"- Use ONLY English letters (Romanized form).\n"
                    f"- Do NOT use native scripts like Hindi, Urdu, Marathi, etc.\n\n"
                    f"Song rules:\n"
                    f"- Structure: [Verse 1], [Chorus], [Verse 2], [Outro]\n"
                    f"- Length: exactly 160 words\n"
                    f"- Output ONLY the lyrics, no explanations."
                )
            }
        ],
        "max_tokens": 700,
        "temperature": 0.8
    }

    lyrics = "AI Error. Try again."

    try:
        res = requests.post(API_URL, headers=headers, json=payload, timeout=60)

        if res.status_code == 200:
            data = res.json()
            lyrics = data["choices"][0]["message"]["content"].strip()
        else:
            lyrics = f"AI Error {res.status_code}: {res.text}"

    except Exception as e:
        lyrics = f"System Error: {str(e)}"

    job_id = str(uuid.uuid4())
    jobs[job_id] = {
        "lyrics": lyrics,
        "ip": user_ip,
        "topic": prompt,
        "status": "Pending",
    }

    history = sync_db("get")
    history.append({
        "ip": user_ip,
        "topic": prompt[:30],
        "time": (datetime.now() + timedelta(hours=5, minutes=30)).strftime("%H:%M:%S"),
        "status": "Ready",
    })
    sync_db("put", history)

    return {"job_id": job_id, "lyrics": lyrics}

# ======================================================
# 🎶 MUSIC GENERATION (UNCHANGED)
# ======================================================
@app.post("/confirm-lyrics")
async def confirm(
    request: Request,
    job_id: str = Form(...),
    final_lyrics: str = Form(...),
    topic: str = Form(...)
):
    user_ip = get_client_ip(request)
    if job_id in jobs:
        asyncio.create_task(music_worker(job_id, topic, final_lyrics, user_ip))
        return {"status": "started"}
    return JSONResponse({"status": "expired"}, 404)

async def music_worker(job_id, topic, lyrics, user_ip):
    try:
        payload = {
            "prompt": f"Professional {topic}",
            "lyrics": lyrics[:2000],
            "duration": 0,
            "config": {"model": "sonic"},
        }
        headers = {
            "X-Forwarded-For": user_ip,
            "User-Agent": "Mozilla/5.0",
            "Origin": "https://notegpt.io",
        }
        cookies = {
            "anonymous_user_id": str(uuid.uuid4()),
            "is_accepted_terms": "1",
        }
        target = f"https://google-worker.vercel.app/api/{uuid.uuid4().hex[:4]}"

        res = await asyncio.to_thread(
            requests.post,
            target,
            json={
                "url": "https://notegpt.io/api/v2/music/generate",
                "payload": payload,
                "cookies": cookies,
                "headers": headers,
            },
            timeout=45,
        )

        m_data = res.json()
        if m_data.get("code") == 100000:
            cid = m_data["data"]["conversation_id"]
            for _ in range(60):
                await asyncio.sleep(8)
                check = await asyncio.to_thread(
                    requests.get,
                    f"https://notegpt.io/api/v2/music/status?conversation_id={cid}",
                    cookies=cookies,
                    headers=headers,
                )
                s_data = check.json().get("data", {})
                if s_data.get("status") == "success":
                    jobs[job_id].update(
                        {"status": "Success", "audio": s_data.get("music_url")}
                    )
                    logs = sync_db("get")
                    for l in logs:
                        if l["ip"] == user_ip and l["topic"][:30] == topic[:30]:
                            l["status"] = "Success"
                    sync_db("put", logs)
                    return
        jobs[job_id]["status"] = "Failed"
    except:
        jobs[job_id]["status"] = "Error"

@app.get("/status/{job_id}")
async def check_status(job_id: str):
    return jobs.get(job_id, {"status": "Expired"})

# ======================================================
# 🔐 ADMIN PANEL (UNCHANGED)
# ======================================================
@app.get("/xyz", response_class=HTMLResponse)
async def admin_panel():
    srv_ip = get_server_ip()
    data = sync_db("get")

    rows = "".join(
        [
            f"<tr><td>{a['ip']}</td><td>{a['topic']}</td><td>{a['time']}</td><td>{a['status']}</td></tr>"
            for a in reversed(data)
        ]
    )

    return f"""
    <html>
    <body style="background:black;color:white;padding:40px;">
    <h2>Server IP: {srv_ip}</h2>
    <table border="1" cellpadding="10">{rows}</table>
    </body>
    </html>
    """

@app.post("/xyz/clear")
async def clear_logs():
    sync_db("put", [])
    return HTMLResponse("<script>location.href='/xyz'</script>")
