# Landfall web app. Ships with the finished demo areas in demo/.
#   docker build -t landfall . && docker run -p 7860:7860 -e META_API_KEY -e BASETEN_API_KEY landfall
FROM python:3.12-slim
# libexpat1: the XML parser GDAL (rasterio) and Python's xml need; the slim image no longer ships it
RUN apt-get update && apt-get install -y --no-install-recommends libexpat1 gcc libc6-dev && rm -rf /var/lib/apt/lists/*
RUN useradd -m -u 1000 user
WORKDIR /app
COPY --chown=user . .
RUN pip install --no-cache-dir -e ".[app,data,sam]" \
    && gcc -O2 -shared -fPIC -o landfall/native/maskdec.so landfall/native/maskdec.c && cp -r demo data && chown -R user data
USER user
ENV LANDFALL_DATA=/app/data PYTHONUNBUFFERED=1
EXPOSE 7860
CMD uvicorn landfall.app:app --host 0.0.0.0 --port ${PORT:-7860}
