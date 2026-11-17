FROM python:3.12-alpine
WORKDIR /app
COPY public-search/ /app/public-search/
COPY recovery/static_search_api.py /app/static_search_api.py
ENV EH_INDEX_DIR=/app/public-search PORT=8080 PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
EXPOSE 8080
USER 10001
CMD ["python", "/app/static_search_api.py"]
