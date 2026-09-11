# syntax=docker/dockerfile:1
#
# A small, isolated service, not part of the main Cadence image, deliberately: yt-dlp's
# YouTube extractor breaks on YouTube's own schedule, not ours, and process isolation keeps
# that breakage from ever touching the rest of the stack. See app/main.py's module docstring.
FROM python:3.12-slim

# ffmpeg is required by yt-dlp's -x/--extract-audio to actually produce an audio file rather
# than erroring out; without it yt-dlp can only save the original container format as-is.
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg \
    && rm -rf /var/lib/apt/lists/*

RUN useradd --create-home --uid 10001 ytdlp
WORKDIR /app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY entrypoint.sh ./
RUN chmod +x entrypoint.sh && mkdir -p /downloads && chown -R ytdlp:ytdlp /app /downloads

USER ytdlp

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8090/health', timeout=3).status == 200 else sys.exit(1)"

EXPOSE 8090
ENTRYPOINT ["./entrypoint.sh"]
