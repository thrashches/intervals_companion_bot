FROM python:3.12-slim

WORKDIR /app

COPY bot/requirements.txt /tmp/requirements.txt
RUN pip install --no-cache-dir -r /tmp/requirements.txt

COPY bot /app

ENV PYTHONUNBUFFERED=1

CMD ["python", "main.py"]
