FROM python:3.11-slim

WORKDIR /app

# psycopg2-binary (pinned in pyproject.toml) ships prebuilt wheels, so no
# build toolchain / libpq-dev is needed here.
COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir .

EXPOSE 8000

CMD ["uvicorn", "src.main:app", "--host", "0.0.0.0", "--port", "8000"]
