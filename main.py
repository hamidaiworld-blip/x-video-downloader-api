import os
import re
import uuid
import shutil
import subprocess
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException, Header, File, Form, UploadFile


app = FastAPI(
    title="UTCutie Video Downloader + Telegram API"
)


DOWNLOAD_DIR = Path("/tmp/xvideos")
DOWNLOAD_DIR.mkdir(
    parents=True,
    exist_ok=True
)

API_KEY = os.getenv(
    "API_KEY",
    ""
)

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
)

TELEGRAM_CHAT_ID = os.getenv(
    "TELEGRAM_CHAT_ID",
    ""
)


X_PATTERN = re.compile(
    r"^https?://(?:www\.)?"
    r"(?:x\.com|twitter\.com)/.+/status/\d+"
)


# ============================================================
# AUTH
# ============================================================

def check_key(x_api_key):

    if API_KEY and x_api_key != API_KEY:

        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )


# ============================================================
# TELEGRAM
# ============================================================

def send_video_to_telegram(
    video_path,
    caption
):

    if not TELEGRAM_BOT_TOKEN:

        raise HTTPException(
            status_code=500,
            detail=(
                "TELEGRAM_BOT_TOKEN "
                "is not configured."
            )
        )

    if not TELEGRAM_CHAT_ID:

        raise HTTPException(
            status_code=500,
            detail=(
                "TELEGRAM_CHAT_ID "
                "is not configured."
            )
        )

    telegram_url = (
        "https://api.telegram.org/bot"
        f"{TELEGRAM_BOT_TOKEN}"
        "/sendVideo"
    )

    try:

        with open(
            video_path,
            "rb"
        ) as video_file:

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
                detail=(
                    "Telegram error: "
                    f"{response.text}"
                )
            )

        result = response.json()

        if not result.get("ok"):

            raise HTTPException(
                status_code=502,
                detail=(
                    "Telegram rejected video: "
                    f"{result}"
                )
            )

        return result

    except requests.RequestException as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "Could not connect to Telegram: "
                f"{str(exc)}"
            )
        )


# ============================================================
# GENERIC DIRECT MEDIA DOWNLOAD
# ============================================================

def download_direct_media(
    media_url,
    output_path
):

    try:

        response = requests.get(
            media_url,
            stream=True,
            timeout=60,
            headers={
                "User-Agent": (
                    "Mozilla/5.0 "
                    "UTCutie-Downloader/1.0"
                )
            }
        )

        if response.status_code != 200:

            raise HTTPException(
                status_code=422,
                detail=(
                    "Could not download "
                    "the media URL. "
                    f"HTTP {response.status_code}"
                )
            )

        content_type = (
            response.headers
            .get(
                "content-type",
                ""
            )
            .lower()
        )

        if (
            "video" not in content_type
            and "octet-stream" not in content_type
        ):

            raise HTTPException(
                status_code=422,
                detail=(
                    "The supplied URL did not "
                    "return a video file."
                )
            )

        max_size = 48 * 1024 * 1024

        content_length = (
            response.headers.get(
                "content-length"
            )
        )

        if content_length:

            try:

                if int(content_length) > max_size:

                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Video exceeds "
                            "the 48 MB limit."
                        )
                    )

            except ValueError:
                pass

        total_bytes = 0

        with open(
            output_path,
            "wb"
        ) as output:

            for chunk in response.iter_content(
                chunk_size=1024 * 1024
            ):

                if not chunk:
                    continue

                total_bytes += len(chunk)

                if total_bytes > max_size:

                    raise HTTPException(
                        status_code=413,
                        detail=(
                            "Video exceeds "
                            "the 48 MB limit."
                        )
                    )

                output.write(chunk)

        if total_bytes == 0:

            raise HTTPException(
                status_code=404,
                detail="Downloaded video is empty."
            )

        return total_bytes

    except requests.RequestException as exc:

        raise HTTPException(
            status_code=502,
            detail=(
                "Could not connect to "
                f"media URL: {str(exc)}"
            )
        )


# ============================================================
# VIDEO VALIDATION
# ============================================================

def validate_video(
    video_path
):

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-show_entries",
        "stream=codec_type",
        "-of",
        "json",
        str(video_path)
    ]

    try:

        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=30
        )

    except subprocess.TimeoutExpired:

        raise HTTPException(
            status_code=408,
            detail="Video validation timed out."
        )

    if result.returncode != 0:

        raise HTTPException(
            status_code=422,
            detail=(
                "Downloaded file is not "
                "a valid video."
            )
        )

    try:

        import json

        metadata = json.loads(
            result.stdout
        )

    except Exception:

        raise HTTPException(
            status_code=422,
            detail=(
                "Could not read video metadata."
            )
        )

    format_data = metadata.get(
        "format",
        {}
    )

    duration = format_data.get(
        "duration"
    )

    if duration is None:

        raise HTTPException(
            status_code=422,
            detail=(
                "Video duration could "
                "not be determined."
            )
        )

    duration = float(
        duration
    )

    if duration < 15:

        raise HTTPException(
            status_code=422,
            detail=(
                f"Video is too short: "
                f"{duration:.2f}s"
            )
        )

    if duration > 180:

        raise HTTPException(
            status_code=422,
            detail=(
                f"Video is too long: "
                f"{duration:.2f}s"
            )
        )

    streams = metadata.get(
        "streams",
        []
    )

    has_video = any(
        isinstance(stream, dict)
        and stream.get("codec_type") == "video"
        for stream in streams
    )

    if not has_video:

        raise HTTPException(
            status_code=422,
            detail="File contains no video stream."
        )

    return duration


# ============================================================
# ROOT / HEALTH
# ============================================================

@app.get("/")
def root():

    return {
        "status": "online",
        "service": (
            "UTCutie Video Downloader "
            "+ Telegram API"
        )
    }


@app.get("/health")
def health():

    return {
        "status": "ok"
    }


# ============================================================
# EXISTING X DOWNLOAD ENDPOINT
# ============================================================

@app.get("/download")
def download(
    url: str,
    x_api_key: str | None = Header(
        default=None
    )
):

    check_key(
        x_api_key
    )

    if not X_PATTERN.match(url):

        raise HTTPException(
            status_code=400,
            detail=(
                "Only X.com/Twitter "
                "status URLs are supported."
            )
        )

    job_id = str(
        uuid.uuid4()
    )

    output_dir = (
        DOWNLOAD_DIR
        / job_id
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    output_template = str(
        output_dir
        / "%(id)s.%(ext)s"
    )

    command = [
        "yt-dlp",
        "--no-playlist",
        "--max-filesize",
        "48M",
        "-f",
        "best[ext=mp4]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        output_template,
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
                detail=(
                    "Could not download "
                    "the X video."
                )
            )

        files = list(
            output_dir.glob("*")
        )

        if not files:

            shutil.rmtree(
                output_dir,
                ignore_errors=True
            )

            raise HTTPException(
                status_code=404,
                detail=(
                    "No video was downloaded."
                )
            )

        video = files[0]

        return {
            "success": True,
            "filename": video.name,
            "download_url": (
                f"/files/{job_id}/"
                f"{video.name}"
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


# ============================================================
# EXISTING X DOWNLOAD + TELEGRAM ENDPOINT
# ============================================================

@app.get("/download-and-send")
def download_and_send(
    url: str,
    caption: str = "",
    x_api_key: str | None = Header(
        default=None
    )
):

    check_key(
        x_api_key
    )

    if not X_PATTERN.match(url):

        raise HTTPException(
            status_code=400,
            detail=(
                "Only X.com/Twitter "
                "status URLs are supported."
            )
        )

    job_id = str(
        uuid.uuid4()
    )

    output_dir = (
        DOWNLOAD_DIR
        / job_id
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    output_template = str(
        output_dir
        / "%(id)s.%(ext)s"
    )

    command = [
        "yt-dlp",
        "--no-playlist",
        "--max-filesize",
        "48M",
        "--match-filter",
        "duration >= 15 & duration <= 180",
        "-f",
        "best[ext=mp4]/best",
        "--merge-output-format",
        "mp4",
        "-o",
        output_template,
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
                detail=(
                    "Video does not meet "
                    "the required duration "
                    "or could not be downloaded."
                )
            )

        files = list(
            output_dir.glob("*")
        )

        if not files:

            shutil.rmtree(
                output_dir,
                ignore_errors=True
            )

            raise HTTPException(
                status_code=404,
                detail=(
                    "No video was downloaded."
                )
            )

        video = files[0]

        telegram_result = (
            send_video_to_telegram(
                video,
                caption
            )
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
            "filename": video.name
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


# ============================================================
# NEW: GENERIC DIRECT MEDIA → TELEGRAM
# ============================================================

@app.post("/upload-and-send")
async def upload_and_send(
    video: UploadFile = File(...),
    caption: str = Form(""),
    x_api_key: str | None = Header(default=None),
):
    """Accept a validated MP4 from GitHub Actions and publish it to Telegram.

    Conversion happens on the GitHub runner to avoid consuming Render's free
    instance CPU for transcoding. This endpoint only accepts MP4 uploads.
    """
    check_key(x_api_key)

    content_type = str(video.content_type or "").casefold()
    if content_type not in {"video/mp4", "application/octet-stream"}:
        raise HTTPException(status_code=415, detail="Only MP4 video uploads are accepted.")

    job_id = str(uuid.uuid4())
    output_dir = DOWNLOAD_DIR / job_id
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = output_dir / "video.mp4"
    max_size = 48 * 1024 * 1024
    total_bytes = 0

    try:
        with video_path.open("wb") as output:
            while True:
                chunk = await video.read(1024 * 1024)
                if not chunk:
                    break
                total_bytes += len(chunk)
                if total_bytes > max_size:
                    raise HTTPException(status_code=413, detail="Video exceeds the 48 MB limit.")
                output.write(chunk)

        if total_bytes == 0:
            raise HTTPException(status_code=422, detail="Uploaded video is empty.")

        duration = validate_video(video_path)
        telegram_result = send_video_to_telegram(video_path, caption)
        message_id = telegram_result.get("result", {}).get("message_id")

        return {
            "success": True,
            "telegram_sent": True,
            "telegram_message_id": message_id,
            "filename": video_path.name,
            "duration": round(duration, 3),
            "file_size": total_bytes,
        }

    finally:
        await video.close()
        shutil.rmtree(output_dir, ignore_errors=True)


@app.get("/send-media-and-send")
def send_media_and_send(
    media_url: str,
    caption: str = "",
    x_api_key: str | None = Header(
        default=None
    )
):

    check_key(
        x_api_key
    )

    job_id = str(
        uuid.uuid4()
    )

    output_dir = (
        DOWNLOAD_DIR
        / job_id
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True
    )

    video_path = (
        output_dir
        / "video.mp4"
    )

    try:

        file_size = download_direct_media(
            media_url,
            video_path
        )

        duration = validate_video(
            video_path
        )

        telegram_result = (
            send_video_to_telegram(
                video_path,
                caption
            )
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
            "filename": video_path.name,
            "duration": round(
                duration,
                3
            ),
            "file_size": file_size
        }

    finally:

        shutil.rmtree(
            output_dir,
            ignore_errors=True
        )
