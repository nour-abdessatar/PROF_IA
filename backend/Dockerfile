FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/models

WORKDIR /app/rag

RUN pip install torch==2.8.0 --index-url https://download.pytorch.org/whl/cpu

COPY rag/requirements.txt .
RUN pip install -r requirements.txt

COPY rag/ /app/rag/
COPY data/ /app/data/

EXPOSE 8001

CMD ["uvicorn", "api:app", "--host", "0.0.0.0", "--port", "8001"]