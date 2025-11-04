import eventlet
eventlet.monkey_patch()

from flask import Flask, render_template, request, jsonify
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_cors import CORS
from werkzeug.security import generate_password_hash, check_password_hash
import pymongo
import redis
import json
import os
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

# ใช้ Eventlet
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

app.secret_key = os.environ.get("SECRET_KEY", "supersecretkey")

# -----------------------------
# DATABASE CONFIG (Updated)
# -----------------------------
MONGO_USERNAME = os.getenv("MONGO_USERNAME")
MONGO_PASSWORD = os.getenv("MONGO_PASSWORD")
MONGO_DB = os.getenv("MONGO_DB", "chat_app")
REDIS_PASSWORD = os.getenv("REDIS_PASSWORD")

MONGO_URI = f"mongodb://{MONGO_USERNAME}:{MONGO_PASSWORD}@mongo-db:27017/{MONGO_DB}?authSource=admin"
REDIS_URL = f"redis://:{REDIS_PASSWORD}@redis-server:6379/0"

mongo_client = pymongo.MongoClient(MONGO_URI)
db = mongo_client[MONGO_DB]
users_collection = db["users"]

r = redis.from_url(REDIS_URL, decode_responses=True)

# -----------------------------
# Routes
# -----------------------------
@app.route('/')
def home():
    return render_template('chat.html')


@app.route('/login')
def login_page():
    return render_template('login.html')


@app.route('/register')
def register_page():
    return render_template('register.html')


# -----------------------------
# Helper: อัพเดตรายชื่อห้อง
# -----------------------------
def emit_room_list():
    emit('room_list', get_all_rooms(), broadcast=True)

# -----------------------------
# Helper: CHAT ROOMS (Redis)
# -----------------------------
def get_room(room_name):
    data = r.get(f"room:{room_name}")
    if not data:
        return None
    try:
        return json.loads(data)
    except json.JSONDecodeError:
        return None

# -----------------------------
# SocketIO events
# -----------------------------
@socketio.on('login')
def handle_login(data):
    username = data.get('username')
    password = data.get('password')

    print(f"🟡 handle_login called from SID: {request.sid}, username={username}")

    user = get_user(username)
    if not user:
        emit('login_response', {'success': False, 'message': 'User does not exist.'}, to=request.sid)
        return
    if not check_password_hash(user['password_hash'], password):
        emit('login_response', {'success': False, 'message': 'Incorrect password.'}, to=request.sid)
        return

    set_user_online(username, True)

    r.hset(f"user:{request.sid}", mapping={"username": username, "room": ""})
    print(f"✅ Redis updated for user:{request.sid}")

    emit('login_response', {'success': True, 'username': username}, to=request.sid)
    emit_room_list()

@socketio.on('reconnect_login')
def handle_reconnect(data):
    username = data.get('username')
    if username:
        r.hset(f"user:{request.sid}", mapping={"username": username, "room": ""})
        print(f"🔁 Reconnected user:{request.sid} as {username}")

@socketio.on('register')
def handle_register(data):
    username = data.get('username')
    password = data.get('password')

    if get_user(username):
        emit('register_response', {'success': False, 'message': 'Username already taken.'}, to=request.sid)
        return

    create_user(username, generate_password_hash(password))
    emit('register_response', {'success': True}, to=request.sid)


@socketio.on('logout')
def handle_logout():
    user_data = r.hgetall(f"user:{request.sid}")
    if not user_data:
        return

    username = user_data.get('username')
    room = user_data.get('room')

    set_user_online(username, False)

    if room:
        leave_room(room)
        room_info = get_room(room)
        if room_info and username in room_info["users"]:
            room_info["users"].remove(username)
            save_room(room, room_info)
            emit("room_users", room_info["users"], room=room)
            emit("user_left", {"user": username, "room": room}, room=room)

    r.delete(f"user:{request.sid}")
    emit_room_list()


@socketio.on('get_rooms')
def handle_get_rooms():
    emit_room_list()


@socketio.on('create_room')
def handle_create_room(data):
    room_name = data.get('room')
    owner = data.get('owner')

    if not room_name or get_room(room_name):
        emit('create_room_response', {'success': False, 'message': 'Room already exists or invalid.'}, to=request.sid)
        return

    room_data = {"owner": owner}
    save_room(room_name, room_data)
    emit('create_room_response', {'success': True, 'room': room_name}, to=request.sid)
    emit_room_list()
    print(f"✅ create_room: {room_name} by {owner}")


@socketio.on('delete_room')
def handle_delete_room(data):
    room_name = data.get('room')
    room = get_room(room_name)
    if not room:
        emit('delete_room_response', {'success': False, 'message': 'Room not found.'}, to=request.sid)
        return

    delete_room(room_name)
    emit('delete_room_response', {'success': True, 'room': room_name}, to=request.sid)
    emit_room_list()


@socketio.on('clear_history')
def handle_clear_history(data):
    room_name = data.get('room')
    if not room_name:
        emit('clear_history_response', {'success': False, 'message': 'Room not specified.'}, to=request.sid)
        return

    clear_room_history(room_name)
    emit('clear_history_response', {'success': True, 'room': room_name}, to=request.sid)
    emit('load_history', [], room=room_name)


@socketio.on('join_room')
def handle_join_room(data):
    room_name = data.get('room')
    if not room_name:
        return

    username = r.hget(f"user:{request.sid}", "username")
    if not username:
        return

    join_room(room_name)
    r.hset(f"user:{request.sid}", "room", room_name)

    # โหลดประวัติแชท
    history_key = f"history:{room_name}"
    history = r.lrange(history_key, 0, -1)
    messages = [json.loads(msg) for msg in history]
    emit("load_history", messages)

    # แจ้งให้ทุกคนในห้องรู้ว่ามีใครอยู่บ้าง (ลบชื่อซ้ำ)
    users_in_room = set()
    for sid in r.keys("user:*"):
        user_room = r.hget(sid, "room")
        if user_room == room_name:
            u = r.hget(sid, "username")
            if u:
                users_in_room.add(u)  # ใช้ set ป้องกันชื่อซ้ำ

    emit("room_users", sorted(list(users_in_room)), to=room_name)

    room_info = get_room(room_name)
    emit("room_info", {"owner": room_info.get("owner", "System")}, to=request.sid)

    print(f"✅ {username} joined room {room_name}")

@socketio.on('send_message')
def handle_message(data):
    room_name = data.get('room') or r.hget(f"user:{request.sid}", "room")
    text = data.get('text', '').strip()
    if not room_name or not text:
        return

    user_data = r.hgetall(f"user:{request.sid}")
    username = user_data.get('username') if user_data else None
    if not username:
        return

    room_info = get_room(room_name)
    if not room_info:
        print(f"⚠️ Room not found: {room_name}")
        return

    # ✅ สร้างข้อความ
    msg = {'user': username, 'text': text}

    # ✅ เก็บประวัติแชทใน Redis
    r.rpush(f"history:{room_name}", json.dumps(msg))

    # ✅ ส่งให้ผู้ใช้ทุกคนในห้อง
    emit('receive_message', msg, room=room_name)
    print(f"💬 [{room_name}] {username}: {text}")

@socketio.on('disconnect')
def handle_disconnect():
    user_data = r.hgetall(f"user:{request.sid}")
    if not user_data:
        print(f"Unknown client disconnected: {request.sid}")
        return

    username = user_data.get('username')
    room = user_data.get('room')

    if username:
        set_user_online(username, False)

    if room:
        room_info = get_room(room)
        if room_info and username in room_info["users"]:
            room_info["users"].remove(username)
            save_room(room, room_info)
            emit('room_users', room_info['users'], room=room)

    r.delete(f"user:{request.sid}")
    print(f"{username or 'Unknown'} disconnected.")

# -----------------------------
# Helper: USERS (MongoDB)
# -----------------------------
def get_user(username):
    return users_collection.find_one({"username": username})

def create_user(username, password_hash):
    users_collection.insert_one({"username": username, "password_hash": password_hash, "online": False})

def set_user_online(username, online=True):
    users_collection.update_one({"username": username}, {"$set": {"online": online}})

def save_room(room_name, room_data):
    r.set(f"room:{room_name}", json.dumps(room_data))

def delete_room(room_name):
    r.delete(f"room:{room_name}")

def clear_room_history(room_name):
    room = get_room(room_name)
    if room:
        room["history"] = []
        save_room(room_name, room)

def get_all_rooms():
    keys = r.keys("room:*")
    rooms = []
    for k in keys:
        room_data = json.loads(r.get(k))
        name = k.split("room:")[1]
        owner = room_data.get("owner", "System")
        rooms.append({"name": name, "owner": owner})
    return rooms

@socketio.on('connect')
def handle_connect():
    print(f"🔌 Client connected: {request.sid}")
    # ตั้งค่าเริ่มต้นใน Redis
    if not r.exists(f"user:{request.sid}"):
        r.hset(f"user:{request.sid}", mapping={"username": "", "room": ""})

if __name__ == '__main__':
    socketio.run(app, host='0.0.0.0', port=8000, debug=True)
