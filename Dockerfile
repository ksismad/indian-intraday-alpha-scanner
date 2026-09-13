FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
RUN mkdir -p /app/runtime
ENV PYTHONUNBUFFERED=1
ENV RESULTS_FILE=/app/runtime/results.json
ENV STATE_FILE=/app/runtime/scanner_state.json
EXPOSE 10000
CMD ["uvicorn", "api_server:app", "--host", "0.0.0.0", "--port", "10000"]
