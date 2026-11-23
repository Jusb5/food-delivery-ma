FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY common common
COPY orders orders
COPY notifications notifications
COPY extension extension
ENV PYTHONUNBUFFERED=1
CMD ["python", "-m", "extension.main", "notifications", "http"]
