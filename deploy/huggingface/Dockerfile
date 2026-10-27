# Hugging Face Space build: fetches Landfall from GitHub at one commit.
# The deploy workflow fills in REF with the commit it deploys.
FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates libexpat1 && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
WORKDIR /app
ARG REPO=https://github.com/harrison0725/Landfall
ARG REF=main
RUN git init -q . && git fetch -q --depth 1 "$REPO" "$REF" && git checkout -q FETCH_HEAD \
    && pip install --no-cache-dir -e ".[app,data,sam]" && cp -r demo data && chown -R user /app
USER user
# Public copy: new assessments need LANDFALL_ACCESS_CODE (a Space secret),
# boxes are capped at 60 buildings and each visitor gets 30 questions an hour.
ENV LANDFALL_DATA=/app/data LANDFALL_MAX_BUILDINGS=60 LANDFALL_TURNS_PER_HOUR=30 PYTHONUNBUFFERED=1
EXPOSE 7860
CMD uvicorn landfall.app:app --host 0.0.0.0 --port ${PORT:-7860}
