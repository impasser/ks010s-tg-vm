FROM python:3.11-slim

# 避免 Python 寫入 .pyc 且輸出不緩衝，方便查看日誌
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# 先複製依賴進行快取
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 複製應用程式程式碼
COPY config.py azure_manager.py bot.py ./

# 啟動機器人 (走 Long Polling，不需 EXPOSE 任何端口)
CMD ["python", "bot.py"]
