# 1. ใช้ Base Image เป็น Python 3.10-slim
FROM python:3.10-slim

# 2. ตั้งค่า Working Directory ภายใน Container
WORKDIR /app

# 3. คัดลอกไฟล์ requirements.txt เข้ามาก่อน
# (เราแยก copy เพื่อใช้ประโยชน์จาก Docker cache)
COPY requirements.txt .

# 4. ติดตั้ง Dependencies
RUN pip install --no-cache-dir -r requirements.txt
RUN pip install watchdog
RUN pip install flask flask-socketio eventlet flask-cors


# 5. คัดลอกไฟล์โค้ดโปรเจกต์ทั้งหมดเข้ามา
COPY . .

# 6. Expose port ที่ Gunicorn จะรัน (เราจะใช้ 8000)
EXPOSE 8000

# หมายเหตุ: เราจะไม่ใส่ CMD ที่นี่
# เพราะเราจะระบุคำสั่ง (command) แยกกันใน docker-compose
# สำหรับ app.py และ worker.py