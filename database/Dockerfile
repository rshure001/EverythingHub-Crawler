FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
RUN pip install --no-cache-dir "psycopg[binary]>=3.2,<4"
COPY database/search_api.py /app/search_api.py
USER 10001:10001
EXPOSE 8080
CMD ["python", "/app/search_api.py"]
