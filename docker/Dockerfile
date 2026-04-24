FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .

# Tăng timeout và cài PyTorch CPU trước (nhẹ hơn)
RUN pip install --default-timeout=1000 -i https://pypi.org/simple/ \
    torch==2.1.0 torchvision==0.16.0 torchaudio==2.1.0 --index-url https://download.pytorch.org/whl/cpu && \
    pip install --default-timeout=1000 -r requirements.txt

COPY . .

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]