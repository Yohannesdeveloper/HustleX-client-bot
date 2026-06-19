# api/main.py
import os, hashlib, hmac, urllib.parse, re
from fastapi import FastAPI, Request, Form, UploadFile, File, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel
import httpx
from typing import Optional
from pymongo import MongoClient
from datetime import datetime
import asyncio
import aiofiles

app = FastAPI()
BOT_TOKEN = os.environ.get("BOT_TOKEN")
CHANNEL_ID = os.environ.get("CHANNEL_ID", "-1003194542999")
MONGODB_URI = os.getenv("MONGODB_URI", "mongodb+srv://yohannesfk123:CKNujByIaepiwyGf@cluster0.mrtm8aj.mongodb.net/hustlex?retryWrites=true&w=majority&appName=Cluster0")

# MongoDB connection with connection pooling
mongo_client = None
db = None

def get_mongodb_connection():
    global mongo_client, db
    try:
        if mongo_client is None:
            # Add connection pooling and timeout settings for faster connections
            mongo_client = MongoClient(
                MONGODB_URI,
                maxPoolSize=50,
                minPoolSize=5,
                connectTimeoutMS=5000,
                socketTimeoutMS=5000,
                serverSelectionTimeoutMS=5000
            )
            db = mongo_client.get_database()
        return db
    except Exception as e:
        print(f"Failed to connect to MongoDB: {e}")
        return None

def verify_init_data(init_data_str: str, bot_token: str) -> bool:
    # init_data_str is the raw query-like string Telegram sends (contains hash param)
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
    # Verify the init_data
    if not verify_init_data(init_data, BOT_TOKEN):
        raise HTTPException(status_code=401, detail="Invalid initData")
    
    # Extract user_id from init_data
    params = dict(urllib.parse.parse_qsl(init_data, keep_blank_values=True))
    user_id = params.get("user")
    if user_id:
        user_id = int(user_id)
    
    # Process the profile data
    profile_data = {
        "name": name,
        "age": age,
        "sex": sex,
    }
    
    # Add contact info if provided
    if contact_info:
        profile_data["contact_info"] = contact_info
    
    # Handle CV file if provided
    if cv_file:
        # Save the CV file asynchronously
        file_path = f"uploads/cv/{cv_file.filename}"
        os.makedirs(os.path.dirname(file_path), exist_ok=True)
        
        content = await cv_file.read()
        async with aiofiles.open(file_path, "wb") as f:
            await f.write(content)
        
        profile_data["cv_file_path"] = file_path
    
    # Handle profile picture if provided
    if profile_pic:
        # Save the profile picture asynchronously
        pic_path = f"uploads/profile_pics/{profile_pic.filename}"
        os.makedirs(os.path.dirname(pic_path), exist_ok=True)
        
        content = await profile_pic.read()
        async with aiofiles.open(pic_path, "wb") as f:
            await f.write(content)
        
        profile_data["profile_pic_file_path"] = pic_path
    
    # Save profile to MongoDB
    database = get_mongodb_connection()
    if database and user_id:
        try:
            # Save user profile
            database.profiles.update_one(
                {"user_id": user_id},
                {"$set": {**profile_data, "updated_at": datetime.utcnow()}},
                upsert=True
            )
            
            # Mark user as registered
            database.registered_users.update_one(
                {"user_id": user_id},
                {"$set": {"user_id": user_id, "registered_at": datetime.utcnow()}},
                upsert=True
            )
            
            print(f"Profile saved and user {user_id} marked as registered")
        except Exception as e:
            print(f"Error saving to MongoDB: {e}")
    
    return {"status": "success", "message": "Profile saved successfully"}
