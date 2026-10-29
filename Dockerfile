FROM python:3.12-slim
WORKDIR /app
COPY pyproject.toml README.md ./
COPY schedule_agent ./schedule_agent
COPY app ./app
COPY demo ./demo
COPY tests ./tests
COPY evals ./evals
RUN pip install --no-cache-dir -e ".[app,test]"
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
