FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt ./
# 国内 ECS 直连 pypi.org 经常超时，强制走清华镜像
RUN pip install \
      --index-url https://pypi.tuna.tsinghua.edu.cn/simple \
      --trusted-host pypi.tuna.tsinghua.edu.cn \
      --timeout 60 \
      --retries 5 \
      -r requirements.txt

COPY app.py auth.py db.py ./
COPY templates ./templates
COPY static ./static

ENV DATA_DIR=/app/data
RUN mkdir -p /app/data/images

EXPOSE 8080

# gthread workers handle SSE long-lived connections cleanly.
# 2 workers x 16 threads = 32 concurrent streams; plenty for 1-machine usage.
CMD ["gunicorn", "-b", "0.0.0.0:8080", \
     "--workers", "2", \
     "--worker-class", "gthread", \
     "--threads", "16", \
     "--timeout", "300", \
     "--graceful-timeout", "30", \
     "--access-logfile", "-", \
     "app:app"]
