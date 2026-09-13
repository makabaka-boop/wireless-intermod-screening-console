FROM python:3.12-slim

WORKDIR /verify
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY verify.py .

CMD ["python", "verify.py"]
