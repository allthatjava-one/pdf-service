##########################################
# Builder: build wheels in a full environment
##########################################
FROM python:3.12-slim AS builder
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Install build dependencies required to build wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       build-essential \
       git \
       libjpeg-dev \
       zlib1g-dev \
       libsndfile1 \
       ffmpeg \
       pkg-config \
       libgl1 \
       libglib2.0-0 \
       libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /wheels

# Copy only requirements and wheel-build them into a dedicated subdir
COPY requirements.txt /wheels/requirements.txt
RUN pip install --upgrade pip setuptools wheel \
    && mkdir -p /wheels/wheels \
    && pip wheel --no-cache-dir --wheel-dir /wheels/wheels -r /wheels/requirements.txt \
    && rm -rf /root/.cache/pip

##########################################
# Final: minimal runtime image with only runtime libs
##########################################
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Install minimal runtime system libs required by wheels
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libjpeg-dev \
       zlib1g-dev \
       libsndfile1 \
       ffmpeg \
       libgl1 \
       libglib2.0-0 \
       libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Copy prebuilt wheels from builder and install them
COPY --from=builder /wheels/wheels /wheels
RUN pip install --no-cache-dir /wheels/*.whl \
    && rm -rf /wheels /root/.cache/pip

# Copy application code (only required files)
COPY main.py /app/main.py
COPY src /app/src
COPY requirements.txt /app/requirements.txt

# Cleanup: remove docs, locales, caches that are not needed at runtime
RUN rm -rf /usr/share/doc /usr/share/man /usr/share/locales/* /var/cache/apt/* /var/lib/apt/lists/* /tmp/*

EXPOSE 8000

# CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
CMD [ "sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}" ]
