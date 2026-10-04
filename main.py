import os
import re
import uuid
import shutil
import subprocess
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Header

app = FastAPI(title="X Video Downloader + Telegram API")

DOWNLOAD_DIR = Path("/tmp/xvideos")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

API_KEY = os.getenv("API_KEY", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

X_PATTERN = re.compile(
    r"^https?://(?:www\.)?(?:x\.com|twitter\.com)/.+/status/\d+"
)


def check_key(x_api_key):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )

def process_video(input_path, output_path):
    command = [
        "ffmpeg",
        "-y",
        "-i", str(input_path),

        # Video
        "-c:v", "libx264",
        "-preset", "veryfast",
        "-crf", "23",

        # Keep original dimensions unless they are unusually large
        "-vf", "scale='min(1280,iw)':-2",

        # Audio
        "-c:a", "aac",
        "-b:a", "128k",

        # Make the MP4 streamable
        "-movflags", "+faststart",

        str(output_path)
    ]

    result = subprocess.run(
        command,
        capture_output=True,
        text=True,
        timeout=300
    )

    if result.returncode != 0:
        raise HTTPException(
            status_code=422,
            detail="Could not process the downloaded video."
        )

    if not output_path.exists():
        raise HTTPException(
            status_code=500,
            detail="Processed video was not created."
        )

    return output_path


def send_video_to_telegram(video_path, caption):
    if not TELEGRAM_BOT_TOKEN:
        raise HTTPException(
            status_code=500,
            detail="TELEGRAM_BOT_TOKEN is not configured."
        )

    if not TELEGRAM_CHAT_ID:
        raise HTTPException(
            status_code=500,
            detail="TELEGRAM_CHAT_ID is not configured."
        )

    telegram_url = (
        f"https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}/sendVideo"
    )

    try:
        with open(video_path, "rb") as video_file:
            response = requests.post(
                telegram_url,
                data={
                    "chat_id": TELEGRAM_CHAT_ID,
                    "caption": caption,
                    "supports_streaming": "true"
                },
                files={
                    "video": (
                        video_path.name,
                        video_file,
                        "video/mp4"
                    )
                },
                timeout=180
            )

        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Telegram error: {response.text}"
            )

        result = response.json()

        if not result.get("ok"):
            raise HTTPException(
                status_code=502,
                detail=f"Telegram rejected video: {result}"
            )

        return result

    except requests.RequestException as e:
        raise HTTPException(
            status_code=502,
            detail=f"Could not connect to Telegram: {str(e)}"
        )


@app.get("/")
def root():
    return {
        "status": "online",
        "service": "X Video Downloader + Telegram API"
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/download")
def download(
    url: str,
    x_api_key: str | None = Header(default=None)
):
    check_key(x_api_key)

    if not X_PATTERN.match(url):
        raise HTTPException(
            status_code=400,
            detail="Only X.com/Twitter status URLs are supported."
        )

    job_id = str(uuid.uuid4())

    output_dir = DOWNLOAD_DIR / job_id
    output_dir.mkdir(parents=True, exist_ok=True)

    output_template = str(
        output_dir / "%(id)s.%(ext)s"
    )

    command = [
        "yt-dlp",
        "--no-playlist",
        "--max-filesize", "48M",
        "-f", "best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        url
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=120
        )

        if result.returncode != 0:
            shutil.rmtree(
                output_dir,
                ignore_errors=True
            )

            raise HTTPException(
                status_code=422,
                detail="Could not download the X video."
            )

        files = list(output_dir.glob("*"))

        if not files:
            shutil.rmtree(
                output_dir,
                ignore_errors=True
            )

            raise HTTPException(
                status_code=404,
                detail="No video was downloaded."
            )

        video = files[0]

        return {
            "success": True,
            "filename": video.name,
            "download_url": (
                f"/files/{job_id}/{video.name}"
            )
        }

    except subprocess.TimeoutExpired:
        shutil.rmtree(
            output_dir,
            ignore_errors=True
        )

        raise HTTPException(
            status_code=408,
            detail="Download timed out."
        )


@app.get("/download-and-send")
def download_and_send(
    url: str,
    caption: str = "",
    x_api_key: str | None = Header(default=None)
):
    check_key(x_api_key)

    if not X_PATTERN.match(url):
        raise HTTPException(
            status_code=400,
            detail="Only X.com/Twitter status URLs are supported."
        )

    job_id = str(uuid.uuid4())

    output_dir = DOWNLOAD_DIR / job_id
    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    output_template = str(
        output_dir / "%(id)s.%(ext)s"
    )

    command = [
        "yt-dlp",
        "--no-playlist",
        "--max-filesize", "48M",
        "-f", "best[ext=mp4]/best",
        "--merge-output-format", "mp4",
        "-o", output_template,
        url
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=300
        )

        if result.returncode != 0:
            shutil.rmtree(
                output_dir,
                ignore_errors=True
            )

            raise HTTPException(
                status_code=422,
                detail="Could not download the X video."
            )

        files = list(output_dir.glob("*"))

        if not files:
            shutil.rmtree(
                output_dir,
                ignore_errors=True
            )

            raise HTTPException(
                status_code=404,
                detail="No video was downloaded."
            )

        video = files[0]

        processed_video = output_dir / "telegram_video.mp4"

        process_video(
            video,
            processed_video
        )

        telegram_result = send_video_to_telegram(
            processed_video,
            caption
        )

        message_id = (
            telegram_result
            .get("result", {})
            .get("message_id")
        )

        return {
            "success": True,
            "telegram_sent": True,
            "telegram_message_id": message_id,
            "filename": processed_video.name
        }

    except subprocess.TimeoutExpired:
        shutil.rmtree(
            output_dir,
            ignore_errors=True
        )

        raise HTTPException(
            status_code=408,
            detail="Download timed out."
        )

    finally:
        shutil.rmtree(
            output_dir,
            ignore_errors=True
        )
