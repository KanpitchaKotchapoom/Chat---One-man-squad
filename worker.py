import os
import json
import redis
import pymongo
import time
from pymongo.errors import PyMongoError
from dotenv import load_dotenv

load_dotenv()

# --- Configuration ---
MONGO_USERNAME = os.getenv("MONGO_USERNAME")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")
MONGO_DB = os.getenv("MONGO_DB", "chat_app")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

MONGO_URL = f"mongodb://{MONGO_USERNAME}:{MONGO_PASSWORD}@mongo-db:27017/{MONGO_DB}?authSource=admin"
REDIS_URL = f"redis://:{REDIS_PASSWORD}@redis-server:6379/0"

TASK_QUEUE_KEY = "chat:task_queue"

# --- Connections ---
print("Worker starting...")

# Connect to Redis
try:
    redis_client = redis.from_url(REDIS_URL, decode_responses=True)
    redis_client.ping()
    print("✅ Worker connected to Redis.")
except Exception as e:
    print(f"❌ Worker ERROR: Could not connect to Redis: {e}")
    exit(1)

# Connect to MongoDB
try:
    mongo_client = pymongo.MongoClient(MONGO_URL)
    db = mongo_client["chat_app"]
    messages_collection = db["messages"]
    print("✅ Worker connected to MongoDB.")
except Exception as e:
    print(f"❌ Worker ERROR: Could not connect to MongoDB: {e}")
    exit(1)


# --- Main Worker Loop ---
def main_loop():
    global redis_client
    print("👂 Worker is listening for tasks...")
    while True:
        try:
            # BLPOP: blocking pop (รอจนกว่าจะมี task เข้ามา)
            task = redis_client.blpop(TASK_QUEUE_KEY, timeout=0)
            if not task:
                continue

            queue_name, raw_data = task
            try:
                message_data = json.loads(raw_data)
            except json.JSONDecodeError:
                print(f"⚠️ Worker: Invalid JSON data skipped: {raw_data}")
                continue

            user = message_data.get("user", "unknown")
            print(f"💾 Worker: Saving message from {user}")

            # Save to MongoDB
            try:
                messages_collection.insert_one(message_data)
            except PyMongoError as e:
                print(f"❌ Worker: MongoDB insert error: {e}")
                time.sleep(2)

        except redis.exceptions.ConnectionError:
            print("⚠️ Worker: Redis connection lost. Retrying in 5s...")
            time.sleep(5)
            try:
                redis_client = redis.from_url(REDIS_URL, decode_responses=True)
                redis_client.ping()
                print("✅ Worker reconnected to Redis.")
            except Exception as e:
                print(f"❌ Worker: Redis reconnect failed: {e}")
        except Exception as e:
            print(f"❌ Worker: Unexpected error: {e}")
            time.sleep(1)


if __name__ == "__main__":
    main_loop()
