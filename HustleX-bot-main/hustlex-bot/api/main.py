# api/main.py
import os, hashlib, hmac, urllib.parse, re, json
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx
from typing import Optional
from pymongo import MongoClient
from datetime import datetime
from pathlib import Path

app = FastAPI()

class RegisterRequest(BaseModel):
    first_name: str
    last_name: str
    gender: str
    dob: str
    country: str
    city: str
    init_data: str

BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1003194542999")

def get_mongodb_connection():
    db_uri = os.environ.get("MONGODB_URI")
    if not db_uri: return None
    client = MongoClient(db_uri)
    return client.get_database("app_db")

def verify_init_data(init_data_str: str, bot_token: str) -> bool:
    params = dict(urllib.parse.parse_qsl(init_data_str, keep_blank_values=True))
    hash_received = params.pop("hash", None)
    if not hash_received:
        return False
    data_check_list = [f"{k}={v}" for k, v in sorted(params.items())]
    data_check_string = "\n".join(data_check_list)
    secret_key = hashlib.sha256(bot_token.encode()).digest()
    hmac_hash = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    return hmac_hash == hash_received

@app.post("/api/profile")
async def save_profile(
    name: str = Form(...),
    age: int = Form(...),
    sex: str = Form(...),
    contact_info: str = Form(None),
    cv_file: UploadFile = File(None),
    profile_pic: UploadFile = File(None),
    init_data: str = Form(...)
):
    if not verify_init_data(init_data, BOT_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid initData")
    
    profile_data = {
        "name": name,
        "age": age,
        "sex": sex,
    }
    
    if contact_info:
        profile_data["contact_info"] = contact_info
    
    if cv_file:
        file_path = f"uploads/cv/{cv_file.filename}"
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        with open(file_path, "wb") as f:
            f.write(await cv_file.read())
        profile_data["cv_file_path"] = file_path
    
    if profile_pic:
        pic_path = f"uploads/profile_pics/{profile_pic.filename}"
        os.makedirs(os.path.dirname(pic_path), exist_ok=True)
        with open(pic_path, "wb") as f:
            f.write(await profile_pic.read())
        profile_data["profile_pic_file_path"] = pic_path
    
    return {"status": "success", "message": "Profile saved successfully"}

@app.post("/api/register")
async def register_user_endpoint(payload: RegisterRequest):
    if not verify_init_data(payload.init_data, BOT_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid initData")
    
    params = dict(urllib.parse.parse_qsl(payload.init_data, keep_blank_values=True))
    user_str = params.get("user")
    user_id = None
    if user_str:
        try:
            user_data = json.loads(user_str)
            user_id = user_data.get("id")
        except Exception:
            try:
                user_id = int(user_str)
            except Exception:
                raise HTTPException(status_code=400, detail="Invalid user ID in initData")
    
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user ID in initData")
    
    profile_data = {
        "name": f"{payload.first_name} {payload.last_name}".strip(),
        "first_name": payload.first_name,
        "last_name": payload.last_name,
        "sex": payload.gender,
        "gender": payload.gender,
        "dob": payload.dob,
        "country": payload.country,
        "city": payload.city,
        "updated_at": datetime.utcnow()
    }
    
    database = get_mongodb_connection()
    if database:
        try:
            database.profiles.update_one(
                {"user_id": user_id},
                {"$set": profile_data},
                upsert=True
            )
            database.registered_users.update_one(
                {"user_id": user_id},
                {"$set": {
                    "user_id": user_id,
                    "first_name": payload.first_name,
                    "last_name": payload.last_name,
                    "registered_at": datetime.utcnow()
                }},
                upsert=True
            )
            print(f"User {user_id} successfully registered via Mini App")
            
            # Send success message to the user via Telegram bot
            success_text = (
                "\u2705 <b>Registration Successful!</b>\n\n"
                f"Welcome, <b>{payload.first_name}</b>! \U0001f389\n\n"
                "You now have full access to HustleX marketplace.\n"
                "Use /start to open the main menu."
            )
            await send_telegram_message(user_id, success_text)
        except Exception as e:
            print(f"Error saving registration to MongoDB: {e}")
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    else:
        raise HTTPException(status_code=500, detail="Could not connect to database")
        
    return {"status": "success", "message": "Registration complete!"}

# ── Serve registration page ────────────────────────────────────────
@app.get("/Register", response_class=HTMLResponse)
@app.get("/register", response_class=HTMLResponse)
async def serve_register_page():
    """Serve the registration form HTML for the Telegram Mini App."""
    html_path = Path(__file__).resolve().parent.parent / "register.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"), status_code=200)

# ── Serve freelancer profile setup page ────────────────────────────────────────
@app.get("/freelancer-profile-setup", response_class=HTMLResponse)
async def serve_freelancer_profile_setup_page():
    """Serve the freelancer profile setup HTML for the Telegram Mini App."""
    html_path = Path(__file__).resolve().parent.parent / "freelancer-profile-setup.html"
    return HTMLResponse(content=html_path.read_text(encoding="utf-8"), status_code=200)

# ── Send Telegram message helper ──────────────────────────────────
async def send_telegram_message(chat_id: int, text: str):
    """Send a message to a Telegram user via the Bot API."""
    if not BOT_TOKEN:
        print("[WARN] BOT_TOKEN not set, cannot send Telegram message")
        return False
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(url, json={
                "chat_id": chat_id,
                "text": text,
                "parse_mode": "HTML"
            })
            data = resp.json()
            if data.get("ok"):
                print(f"Telegram message sent to {chat_id}")
            else:
                print(f"Failed to send Telegram message to {chat_id}: {data.get('description')}")
            return data.get("ok", False)
    except Exception as e:
        print(f"Error sending Telegram message to {chat_id}: {e}")
        return False

@app.post("/api/freelancer-profile")
async def save_freelancer_profile(request: Request):
    form = await request.form()
    init_data = form.get("initData")
    
    if not init_data or not verify_init_data(init_data, BOT_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid initData")
    
    params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    user_str = params.get("user")
    user_id = None
    if user_str:
        try:
            user_data = json.loads(user_str)
            user_id = user_data.get("id")
        except Exception:
            pass
            
    if not user_id:
        raise HTTPException(status_code=400, detail="Missing user ID")

    # Extract dynamic lists
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

    # Save Profile Picture
    profile_pic = form.get("profile_pic")
    pic_path = None
    if profile_pic and hasattr(profile_pic, "filename") and profile_pic.filename:
        pic_path = f"uploads/profile_pics/{user_id}_{profile_pic.filename}"
        os.makedirs(os.path.dirname(pic_path), exist_ok=True)
        with open(pic_path, "wb") as f:
            f.write(await profile_pic.read())

    # Save Cover Image
    cover_img = form.get("cover_img")
    cover_path = None
    if cover_img and hasattr(cover_img, "filename") and cover_img.filename:
        cover_path = f"uploads/covers/{user_id}_{cover_img.filename}"
        os.makedirs(os.path.dirname(cover_path), exist_ok=True)
        with open(cover_path, "wb") as f:
            f.write(await cover_img.read())

    profile_data = {
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
        "updated_at": datetime.utcnow()
    }
    
    if pic_path:
        profile_data["profile_pic_path"] = pic_path
    if cover_path:
        profile_data["cover_img_path"] = cover_path

    database = get_mongodb_connection()
    if database:
        try:
            database.freelancer_profiles.update_one(
                {"user_id": user_id},
                {"$set": profile_data},
                upsert=True
            )
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    else:
        raise HTTPException(status_code=500, detail="Could not connect to database")

    return {"status": "success", "message": "Profile created"}

# ── Vercel ASGI handler ───────────────────────────────────────────
try:
    from mangum import Mangum
    handler = Mangum(app)
except ImportError:
    # Mangum not available (e.g. running locally with uvicorn)
    handler = None
