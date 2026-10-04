import os
import re
import uuid
import shutil
import subprocess
from pathlib import Path

from fastapi import FastAPI, HTTPException, Header
from fastapi.responses import FileResponse

app = FastAPI(title="X Video Downloader API")

DOWNLOAD_DIR = Path("/tmp/xvideos")
DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)

API_KEY = os.getenv("API_KEY", "")

X_PATTERN = re.compile(
    r"^https?://(?:www\.)?(?:x\.com|twitter\.com)/.+/status/\d+"
)


def check_key(x_api_key):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

@app.get("/")
def root():
    return {
        "status": "online",
        "service": "X Video Downloader API"
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

    output_template = str(output_dir / "%(id)s.%(ext)s")

    command = [
        "yt-dlp",
        "--no-playlist",
        "--max-filesize", "50M",
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
            shutil.rmtree(output_dir, ignore_errors=True)
            raise HTTPException(
                status_code=422,
                detail="Could not download the X video."
            )

        files = list(output_dir.glob("*"))

        if not files:
            shutil.rmtree(output_dir, ignore_errors=True)
            raise HTTPException(
                status_code=404,
                detail="No video was downloaded."
            )

        video = files[0]

        return {
            "success": True,
            "filename": video.name,
            "download_url": f"/files/{job_id}/{video.name}"
        }

    except subprocess.TimeoutExpired:
        shutil.rmtree(output_dir, ignore_errors=True)
        raise HTTPException(
            status_code=408,
            detail="Download timed out."
        )


@app.get("/files/{job_id}/{filename}")
def get_file(job_id: str, filename: str):
    file_path = DOWNLOAD_DIR / job_id / filename

    if not file_path.exists():
        raise HTTPException(status_code=404, detail="File not found")

    return FileResponse(
        file_path,
        media_type="video/mp4",
        filename=filename
    )
