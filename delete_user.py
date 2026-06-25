import re
from pymongo import MongoClient
uri = "mongodb+srv://yohannesfk123:CKNujByIaepiwyGf@cluster0.mrtm8aj.mongodb.net/hustlex?retryWrites=true&w=majority&appName=Cluster0"
db = MongoClient(uri)["hustlex"]

# Find user by phone (partial match)
all_profiles = list(db.profiles.find())
uid = None
for p in all_profiles:
    phone = p.get("phone") or p.get("phone_number") or ""
    name = p.get("name", "")
    if "0942927999" in phone.replace(" ", "").replace("+", ""):
        uid = p["user_id"]
        print(f"Found by phone: user_id={uid}, name={name}, phone={phone}")
        break
    if "yohannes" in name.lower():
        uid = p["user_id"]
        print(f"Found by name: user_id={uid}, name={name}, phone={phone}")
        break

if not uid:
    all_reg = list(db.registered_users.find())
    for r in all_reg:
        fn = r.get("first_name", "")
        ln = r.get("last_name", "")
        if "yohannes" in fn.lower() or "yohannes" in ln.lower():
            uid = r["user_id"]
            print(f"Found in registered_users: user_id={uid}, name={fn} {ln}")
            break

if uid:
    dr = db.registered_users.delete_one({"user_id": uid})
    dp = db.profiles.delete_one({"user_id": uid})
    print(f"Deleted: registered_users={dr.deleted_count}, profiles={dp.deleted_count}")
else:
    print("User not found in any collection")
