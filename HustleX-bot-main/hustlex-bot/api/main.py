# api/main.py
import os, hashlib, hmac, urllib.parse, re, json
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx
from typing import Optional
from pymongo import MongoClient
from datetime import datetime

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
        except Exception as e:
            print(f"Error saving registration to MongoDB: {e}")
            raise HTTPException(status_code=500, detail=f"Database error: {str(e)}")
    else:
        raise HTTPException(status_code=500, detail="Could not connect to database")
        
    return {"status": "success", "message": "Registration complete!"}
