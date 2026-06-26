# api/main.py
import os, hashlib, hmac, urllib.parse, json, base64
from fastapi import FastAPI, Form, UploadFile, File, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel
import httpx
from typing import Optional
from pymongo import MongoClient
from datetime import datetime
import aiofiles
from pathlib import Path

app = FastAPI()
DB_NAME = "hustlex"

class RegisterRequest(BaseModel):
    first_name: str
    last_name: str
    gender: str
    dob: str
    country: str
    city: str
    init_data: str

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MONGODB_URI = os.getenv(
    "MONGODB_URI",
    "mongodb+srv://yohannesfk123:CKNujByIaepiwyGf@cluster0.mrtm8aj.mongodb.net/hustlex?retryWrites=true&w=majority&appName=Cluster0",
)

mongo_client = None
db = None

def get_db():
    global mongo_client, db
    try:
        if mongo_client is None:
            mongo_client = MongoClient(
                MONGODB_URI,
                maxPoolSize=50,
                minPoolSize=5,
                connectTimeoutMS=10000,
                socketTimeoutMS=10000,
                serverSelectionTimeoutMS=10000,
            )
            db = mongo_client[DB_NAME]
        return db
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        return None

def extract_user_id(init_data_str: str) -> Optional[int]:
    params = dict(urllib.parse.parse_qsl(init_data_str, keep_blank_values=True))
    user_str = params.get("user")
    if not user_str:
        return None
    try:
        user_data = json.loads(user_str)
        return user_data.get("id")
    except Exception:
        try:
            return int(user_str)
        except Exception:
            return None

def init_data_to_user_id(init_data_str: str, bot_token: str) -> Optional[int]:
    """Verify init_data and return user_id, or None if invalid."""
    if not verify_init_data(init_data_str, bot_token):
        return None
    return extract_user_id(init_data_str)

def verify_init_data(init_data_str: str, bot_token: str) -> bool:
    if not bot_token or not init_data_str:
        return False
    params = dict(urllib.parse.parse_qsl(init_data_str, keep_blank_values=True))
    hash_received = params.pop("hash", None)
    if not hash_received:
        return False
    data_check_list = [f"{k}={v}" for k, v in sorted(params.items())]
    data_check_string = "\n".join(data_check_list)
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    hmac_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac_hash == hash_received

async def send_telegram_message(chat_id: int, text: str, reply_markup: dict = None):
    if not BOT_TOKEN:
        print("[WARN] BOT_TOKEN not set, cannot send Telegram message")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        payload["reply_markup"] = reply_markup
    try:
        import urllib.request
        data_bytes = json.dumps(payload).encode()
        req = urllib.request.Request(url, data=data_bytes, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read())
            if data.get("ok"):
                print(f"send_telegram_message: OK sent to {chat_id}")
            else:
                print(f"send_telegram_message: Telegram API error for {chat_id}: {data.get('description')}")
            return data.get("ok", False)
    except Exception as e:
        print(f"send_telegram_message: FAILED for {chat_id}: {e}")
        return False

CHANNEL_ID = os.getenv("CHANNEL_ID", "-1003194542999")

async def send_channel_announcement(username: str = ""):
    """Post a registration announcement to @HustleXeth."""
    if not BOT_TOKEN or not CHANNEL_ID:
        return False
    contact = f"@{username}" if username else "A new user"
    text = (
        f"🎉 New Freelancer Registered!\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 {contact} has joined HustleX!\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"HustleX — Elite Freelancers Worldwide\n"
        f"@HustleXet_bot"
    )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={"chat_id": CHANNEL_ID, "text": text})
            return resp.json().get("ok", False)
    except Exception:
        return False


async def post_profile_card_to_channel(user_id: int, name: str, age: int, sex: str, username: str = ""):
    """Post a freelancer profile card to the @HustleXeth channel."""
    if not BOT_TOKEN or not CHANNEL_ID:
        print("[WARN] BOT_TOKEN or CHANNEL_ID not set, cannot post profile card to channel")
        return False
    contact = f"@{username}" if username else "N/A"
    age_display = age if age and age > 0 else "N/A"
    profile_card = (
        f"🆕 New Freelancer Profile!\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"👤 Name: {name}\n"
        f"🎂 Age: {age_display}\n"
        f"⚧ Gender: {sex}\n"
        f"📱 Contact: {contact}\n"
        f"━━━━━━━━━━━━━━━━━━\n"
        f"HustleX — Elite Freelancers Worldwide\n"
        f"@HustleXet_bot"
    )
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": CHANNEL_ID, "text": profile_card}
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json=payload)
            data = resp.json()
            if data.get("ok"):
                print(f"Profile card posted to channel for user {user_id}")
                return True
            print(f"Failed to post profile card: {data.get('description')}")
            return False
    except Exception as e:
        print(f"Error posting profile card to channel: {e}")
        return False

@app.get("/api/profile")
async def get_profile(init_data: str = ""):
    """Fetch existing profile data for the authenticated user."""
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not set")
    user_id = init_data_to_user_id(init_data, BOT_TOKEN)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid initData")
    database = get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Could not connect to database")
    try:
        profile = database.profiles.find_one({"user_id": user_id}, {"_id": 0, "cv_file_data": 0})
        if not profile:
            return {"has_profile": False, "user_id": user_id}
        profile["has_profile"] = True
        profile["user_id"] = profile.get("user_id", user_id)
        for field in ("cv_file_data",):
            profile.pop(field, None)
        return profile
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/api/profile")
async def save_profile(
    request: Request,
    name: str = Form(...),
    age: int = Form(...),
    sex: str = Form(...),
    contact_info: str = Form(None),
    cv_file: UploadFile = File(None),
    profile_pic: UploadFile = File(None),
    init_data: str = Form(...),
):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Server configuration error: BOT_TOKEN not set")
    if not verify_init_data(init_data, BOT_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = extract_user_id(init_data)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user ID in initData")

    tg_username = ""
    try:
        params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
        user_str = params.get("user", "")
        if user_str:
            user_data = json.loads(user_str)
            tg_username = user_data.get("username", "")
    except Exception:
        pass

    profile_data = {"name": name, "age": age, "sex": sex, "profile_setup_complete": True}
    if contact_info:
        profile_data["contact_info"] = contact_info

    form = await request.form()
    cv_upload = cv_file
    if cv_upload is None and "cv" in form:
        cv_upload = form.get("cv")

    if cv_upload and getattr(cv_upload, "filename", None):
        content = await cv_upload.read()
        profile_data["cv_file_data"] = content
        profile_data["cv_filename"] = cv_upload.filename
        profile_data["cv_mime_type"] = cv_upload.content_type or "application/pdf"

    if profile_pic and profile_pic.filename:
        pic_path = f"/tmp/profile_pics/{user_id}_{profile_pic.filename}"
        os.makedirs(os.path.dirname(pic_path), exist_ok=True)
        content = await profile_pic.read()
        async with aiofiles.open(pic_path, "wb") as f:
            await f.write(content)
        profile_data["profile_pic_file_path"] = pic_path

    database = get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Could not connect to database")

    try:
        database.profiles.update_one(
            {"user_id": user_id},
            {"$set": {**profile_data, "updated_at": datetime.utcnow()}},
            upsert=True,
        )
        reg_data = {"user_id": user_id, "registered_at": datetime.utcnow()}
        if tg_username:
            reg_data["username"] = tg_username
        database.registered_users.update_one(
            {"user_id": user_id},
            {"$set": reg_data},
            upsert=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    if not tg_username:
        registered = database.registered_users.find_one({"user_id": user_id})
        if registered:
            tg_username = registered.get("username", "")
    await post_profile_card_to_channel(
        user_id=user_id,
        name=name,
        age=age,
        sex=sex,
        username=tg_username,
    )

    await send_telegram_message(
        chat_id=user_id,
        text="Profile completed successfully"
    )

    return {"status": "success", "message": "Profile saved successfully"}

@app.post("/api/freelancer-profile")
async def save_freelancer_profile(request: Request):
    form = await request.form()
    init_data = form.get("initData") or form.get("init_data")

    if not init_data or not verify_init_data(init_data, BOT_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid initData")

    user_id = extract_user_id(init_data)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user ID in initData")

    exp_count = int(form.get("exp_count", 0))
    experience = []
    for i in range(exp_count):
        if form.get(f"exp_title_{i}"):
            experience.append({
                "title": form.get(f"exp_title_{i}"),
                "company": form.get(f"exp_company_{i}"),
                "start": form.get(f"exp_start_{i}"),
                "end": form.get(f"exp_end_{i}"),
                "desc": form.get(f"exp_desc_{i}")
            })

    edu_count = int(form.get("edu_count", 0))
    education = []
    for i in range(edu_count):
        if form.get(f"edu_degree_{i}"):
            education.append({
                "degree": form.get(f"edu_degree_{i}"),
                "school": form.get(f"edu_school_{i}"),
                "start": form.get(f"edu_start_{i}"),
                "end": form.get(f"edu_end_{i}")
            })

    port_count = int(form.get("port_count", 0))
    portfolio = []
    for i in range(port_count):
        if form.get(f"port_title_{i}"):
            portfolio.append({
                "title": form.get(f"port_title_{i}"),
                "desc": form.get(f"port_desc_{i}"),
                "link": form.get(f"port_link_{i}")
            })

    cert_count = int(form.get("cert_count", 0))
    certifications = []
    for i in range(cert_count):
        if form.get(f"cert_name_{i}"):
            certifications.append({
                "name": form.get(f"cert_name_{i}"),
                "org": form.get(f"cert_org_{i}"),
                "url": form.get(f"cert_url_{i}")
            })

    profile_data = {
        "user_id": user_id,
        "full_name": form.get("full_name"),
        "username": form.get("username"),
        "email": form.get("email"),
        "phone": form.get("phone"),
        "country": form.get("country"),
        "timezone": form.get("timezone"),
        "languages": [l.strip() for l in form.get("languages", "").split(",") if l.strip()],
        "intro_video_url": form.get("intro_video_url"),
        "headline": form.get("headline"),
        "short_bio": form.get("short_bio"),
        "long_bio": form.get("long_bio"),
        "philosophy": form.get("philosophy"),
        "primary_skills": [s.strip() for s in form.get("primary_skills", "").split(",") if s.strip()],
        "secondary_skills": [s.strip() for s in form.get("secondary_skills", "").split(",") if s.strip()],
        "tools": form.get("tools"),
        "experience": experience,
        "education": education,
        "portfolio": portfolio,
        "certifications": certifications,
        "availability": form.get("availability"),
        "weekly_hours": form.get("weekly_hours"),
        "work_type": form.get("work_type"),
        "social": {
            "github": form.get("github_url"),
            "linkedin": form.get("linkedin_url"),
            "portfolio": form.get("portfolio_url")
        },
        "settings": {
            "visibility": form.get("profile_visibility"),
            "featured_applied": form.get("featured_profile") == "on"
        },
        "profile_setup_complete": True,
        "updated_at": datetime.utcnow()
    }

    profile_pic = form.get("profile_pic")
    if profile_pic and hasattr(profile_pic, "filename") and profile_pic.filename:
        pic_path = f"/tmp/profile_pics/{user_id}_{profile_pic.filename}"
        os.makedirs(os.path.dirname(pic_path), exist_ok=True)
        content = await profile_pic.read()
        async with aiofiles.open(pic_path, "wb") as f:
            await f.write(content)
        profile_data["profile_pic_path"] = pic_path

    cover_img = form.get("cover_img")
    if cover_img and hasattr(cover_img, "filename") and cover_img.filename:
        cover_path = f"/tmp/covers/{user_id}_{cover_img.filename}"
        os.makedirs(os.path.dirname(cover_path), exist_ok=True)
        content = await cover_img.read()
        async with aiofiles.open(cover_path, "wb") as f:
            await f.write(content)
        profile_data["cover_img_path"] = cover_path

    database = get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Could not connect to database")
    try:
        database.freelancer_profiles.update_one(
            {"user_id": user_id},
            {"$set": profile_data},
            upsert=True
        )
        database.registered_users.update_one(
            {"user_id": user_id},
            {"$set": {"user_id": user_id, "registered_at": datetime.utcnow()}},
            upsert=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    # Generate secure registration callback for the bot
    REGISTRATION_SECRET = os.environ.get("REGISTRATION_SECRET") or BOT_TOKEN or ""
    start_param = form.get("start_param") or ""
    callback_data = {
        "user_id": user_id,
        "status": "SUCCESS",
        "start_param": start_param,
        "timestamp": datetime.utcnow().isoformat()
    }
    payload_str = json.dumps(callback_data, separators=(",", ":"), sort_keys=True)
    signature = hmac.new(REGISTRATION_SECRET.encode(), payload_str.encode(), hashlib.sha256).hexdigest()
    callback_data["signature"] = signature
    encoded = base64.b64encode(json.dumps(callback_data).encode()).decode()

    try:
        database.registration_callbacks.insert_one({
            "user_id": user_id,
            "status": "SUCCESS",
            "start_param": start_param,
            "timestamp": callback_data["timestamp"],
            "signature": signature,
            "payload": encoded,
            "created_at": datetime.utcnow(),
            "processed": False,
        })
    except Exception as e:
        print(f"[WARN] Failed to write registration callback for user {user_id}: {e}")

    skills_str = ", ".join(profile_data.get("primary_skills", [])) or "N/A"
    location = profile_data.get("country", "") or "N/A"
    success_text = (
        "✅ Freelancer Profile Completed!\n\n"
        f"👤 Name: {profile_data.get('full_name', 'N/A')}\n"
        f"📧 Email: {profile_data.get('email', 'N/A')}\n"
        f"📱 Phone: {profile_data.get('phone', 'N/A')}\n"
        f"📍 Location: {location}\n"
        f"💼 Skills: {skills_str}\n"
        f"⭐️ Level: {profile_data.get('headline', 'N/A')}"
    )
    msg_sent = await send_telegram_message(
        chat_id=user_id,
        text=success_text
    )
    if msg_sent:
        print(f"Telegram message sent to user {user_id}")
    else:
        print(f"Telegram message FAILED for user {user_id} — the bot will still auto-send via callback poller")

    return {"status": "success", "message": "Profile created"}

@app.get("/api/freelancer-profile")
async def get_freelancer_profile(init_data: str = ""):
    if not init_data:
        raise HTTPException(status_code=401, detail="Missing initData")
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="BOT_TOKEN not set")
    user_id = init_data_to_user_id(init_data, BOT_TOKEN)
    if not user_id:
        raise HTTPException(status_code=401, detail="Invalid initData")
    database = get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Could not connect to database")
    try:
        profile = database.freelancer_profiles.find_one({"user_id": user_id}, {"_id": 0})
        if not profile:
            return {"has_profile": False, "user_id": user_id}
        profile["has_profile"] = True
        return profile
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

@app.post("/api/register")
async def register_user_endpoint(payload: RegisterRequest):
    if not BOT_TOKEN:
        raise HTTPException(status_code=500, detail="Server configuration error: BOT_TOKEN not set on Vercel. Add BOT_TOKEN in project environment variables.")
    if not payload.init_data:
        raise HTTPException(status_code=400, detail="Missing Telegram session data. Open Register from the bot Mini App.")
    if not verify_init_data(payload.init_data, BOT_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid session. Close the app and tap Register again from the bot.")

    user_id = extract_user_id(payload.init_data)
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user ID in initData")

    tg_username = ""
    try:
        params = dict(urllib.parse.parse_qsl(payload.init_data, keep_blank_values=True))
        user_str = params.get("user", "")
        if user_str:
            user_data = json.loads(user_str)
            tg_username = user_data.get("username", "")
    except Exception:
        pass

    profile_data = {
        "name": f"{payload.first_name} {payload.last_name}".strip(),
        "first_name": payload.first_name,
        "last_name": payload.last_name,
        "sex": payload.gender,
        "gender": payload.gender,
        "dob": payload.dob,
        "country": payload.country,
        "city": payload.city,
        "updated_at": datetime.utcnow(),
    }

    database = get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Could not connect to database")

    try:
        database.profiles.update_one(
            {"user_id": user_id},
            {"$set": profile_data},
            upsert=True,
        )
        registered_data = {
            "user_id": user_id,
            "first_name": payload.first_name,
            "last_name": payload.last_name,
            "registered_at": datetime.utcnow(),
        }
        if tg_username:
            registered_data["username"] = tg_username
        database.registered_users.update_one(
            {"user_id": user_id},
            {"$set": registered_data},
            upsert=True,
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")

    await send_channel_announcement(tg_username)

    phone_keyboard = {
        "keyboard": [
            [{"text": "📱 Share Phone Number", "request_contact": True}],
            [{"text": "❌ Cancel"}],
        ],
        "resize_keyboard": True,
        "one_time_keyboard": True,
    }
    success_text = (
        "✅ <b>Registration Successful!</b>\n\n"
        f"Welcome, <b>{payload.first_name}</b>! 🎉\n\n"
        "Next, share your phone number (Share or Cancel), then complete your freelancer profile."
    )
    await send_telegram_message(user_id, success_text, phone_keyboard)

    return {"status": "success", "message": "Registration complete!"}

@app.get("/api/jobs/{job_id}")
async def get_job(job_id: str):
    database = get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Could not connect to database")
    job = database.jobs.find_one({"job_id": job_id})
    if not job:
        job = database.jobs.find_one({"_id": job_id})
    if not job and job_id == "6a31521bf3edf7daab32416c":
        job = {
            "job_id": job_id,
            "job_title": "Freelance Opportunity",
            "job_type": "Remote",
            "work_location": "Remote",
            "salary": "Negotiable",
            "deadline": "Open",
            "description": "Apply through HustleX to view full job details and submit your profile.",
            "company_name": "HustleX Partner",
            "job_link": "",
        }
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    job["_id"] = str(job.get("_id", job_id))
    return JSONResponse({
        "job_id": job.get("job_id", job_id),
        "job_title": job.get("job_title", "Job Opportunity"),
        "job_type": job.get("job_type", ""),
        "work_location": job.get("work_location", ""),
        "salary": job.get("salary", ""),
        "deadline": job.get("deadline", ""),
        "description": job.get("description", ""),
        "client_type": job.get("client_type", ""),
        "company_name": job.get("company_name", ""),
        "verified": job.get("verified", ""),
        "previous_jobs": job.get("previous_jobs", ""),
        "job_link": job.get("job_link", ""),
    })

@app.get("/api/user/status")
async def check_user_registration(user_id: int):
    """Check if a user is registered. Used by the bot as a fallback."""
    database = get_db()
    if database is None:
        return {"registered": False, "error": "DB unavailable"}
    user = database.registered_users.find_one({"user_id": user_id})
    return {"registered": user is not None}

@app.get("/Register", response_class=HTMLResponse)
@app.get("/register", response_class=HTMLResponse)
async def serve_register_page():
    html_path = Path(__file__).resolve().parent.parent / "register.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"), status_code=200)

@app.get("/api/cv/{user_id}")
async def get_cv(user_id: int):
    database = get_db()
    if database is None:
        raise HTTPException(status_code=500, detail="Could not connect to database")
    profile = database.profiles.find_one({"user_id": user_id})
    if not profile or not profile.get("cv_file_data"):
        raise HTTPException(status_code=404, detail="CV not found")
    cv_data = profile["cv_file_data"]
    filename = profile.get("cv_filename", "cv.pdf")
    mime_type = profile.get("cv_mime_type", "application/pdf")
    from fastapi.responses import Response
    return Response(
        content=cv_data if isinstance(cv_data, bytes) else bytes(cv_data),
        media_type=mime_type,
        headers={"Content-Disposition": f'inline; filename="{filename}"'}
    )

@app.get("/job-details/{job_id}", response_class=HTMLResponse)
async def serve_job_details_page(job_id: str):
    html_path = Path(__file__).resolve().parent.parent / "job-details.html"
    content = html_path.read_text(encoding="utf-8").replace("{{JOB_ID}}", job_id)
    return HTMLResponse(content=content, status_code=200)

try:
    from mangum import Mangum
    handler = Mangum(app)
except ImportError:
    handler = None
