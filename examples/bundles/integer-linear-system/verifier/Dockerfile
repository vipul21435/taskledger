# Grader image. The build context is tests/; the agent's /app is mounted at run time.
FROM python:3.12-slim@sha256:f77ac9e44ae96ef2c90b8053ea08c31f8be030f824196b0ae4db6d462c84e51f
RUN pip install --no-cache-dir pytest==8.3.5
WORKDIR /grader
COPY . /grader/tests/
ENV TASK_WORKDIR=/app
CMD ["pytest", "-q", "-p", "no:cacheprovider", "tests"]
