"""
StreamGrabber — video downloader backend.

Local:  pip install -r requirements.txt
        python app.py            → http://127.0.0.1:5000
Cloud:  deploy with the included Dockerfile (see README.md)

Environment variables (all optional):
  PORT              port to listen on (cloud hosts set this automatically)
  MAX_JOBS          downloads allowed at the same time (default 3)
  MAX_FILESIZE_MB   reject files bigger than this (default 1500)
  YTDLP_COOKIES     path to a cookies.txt file, helps when YouTube blocks the server
"""
import os
import re
import shutil
import tempfile
import threading
import time
import uuid

import yt_dlp
from flask import Flask, Response, abort, jsonify, request, send_file, send_from_directory

BASE = os.path.dirname(os.path.abspath(__file__))
HAS_FFMPEG = shutil.which("ffmpeg") is not None
JOB_TTL = 60 * 30
MAX_JOBS = int(os.environ.get("MAX_JOBS", "3"))
MAX_FILESIZE = int(os.environ.get("MAX_FILESIZE_MB", "1500")) * 1024 * 1024
COOKIES = os.environ.get("YTDLP_COOKIES")
if COOKIES and not os.path.isfile(COOKIES):
    COOKIES = None

app = Flask(__name__)
JOBS = {}
LOCK = threading.Lock()


# ---------- helpers ----------

def base_opts():
    opts = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if COOKIES:
        opts["cookiefile"] = COOKIES
    return opts


def clean_error(e):
    msg = re.sub(r"\x1b\[[0-9;]*m", "", str(e))
    msg = msg.replace("ERROR: ", "").strip()
    low = msg.lower()
    if "unsupported url" in low:
        return "That site or link isn't supported."
    if "not a bot" in low:
        return "YouTube is blocking this server. Try again later or try a different site."
    if "unable to download webpage" in low or "unable to connect" in low or "timed out" in low:
        return "Couldn't reach that site. Check the link and try again."
    if "private video" in low or "sign in" in low or "login" in low:
        return "This video is private or requires sign-in."
    if "max-filesize" in low or "larger than max" in low:
        return "That file is too large for this server. Pick a lower quality."
    if "ffmpeg" in low:
        return "ffmpeg is required for this format."
    if "unable to download webpage" in low or "timed out" in low:
        return "Couldn't reach that site. Check the link and try again."
    return msg[:300] or "Something went wrong."


def fmt_size(f):
    return f.get("filesize") or f.get("filesize_approx") or 0


def quality_label(h):
    if h >= 2160:
        return "4K Ultra HD"
    if h >= 1440:
        return "2K QHD"
    if h >= 1080:
        return "Full HD"
    if h >= 720:
        return "HD"
    return "SD"


def active_jobs():
    return sum(1 for j in JOBS.values() if j["status"] in ("queued", "downloading", "processing"))


def cleanup_loop():
    while True:
        time.sleep(120)
        now = time.time()
        with LOCK:
            stale = [k for k, j in JOBS.items() if now - j["created"] > JOB_TTL]
            for k in stale:
                shutil.rmtree(JOBS[k]["dir"], ignore_errors=True)
                del JOBS[k]


# start cleanup at import so it also runs under gunicorn
threading.Thread(target=cleanup_loop, daemon=True).start()


@app.after_request
def cors(resp):
    # lets the page work even if it's opened from a different address
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    return resp


# ---------- pages ----------

@app.get("/")
def index():
    return send_from_directory(BASE, "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return jsonify({
        "name": "StreamGrabber",
        "short_name": "StreamGrabber",
        "start_url": "/",
        "display": "standalone",
        "background_color": "#07070a",
        "theme_color": "#07070a",
        "icons": [{"src": "/icon.svg", "sizes": "any", "type": "image/svg+xml", "purpose": "any maskable"}],
    })


ICON = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 512 512">
<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">
<stop offset="0" stop-color="#ff4d6d"/><stop offset="1" stop-color="#ff8a3d"/></linearGradient></defs>
<rect width="512" height="512" rx="112" fill="url(#g)"/>
<path d="M256 120v200m0 0-80-80m80 80 80-80M150 392h212" stroke="#fff" stroke-width="44"
 stroke-linecap="round" stroke-linejoin="round" fill="none"/></svg>"""


@app.get("/icon.svg")
def icon():
    return Response(ICON, mimetype="image/svg+xml")


@app.get("/api/health")
def health():
    return jsonify(ok=True, ffmpeg=HAS_FFMPEG)


# ---------- api ----------

@app.route("/api/info", methods=["POST", "OPTIONS"])
def info():
    if request.method == "OPTIONS":
        return "", 204
    url = ((request.get_json(silent=True) or {}).get("url") or "").strip()
    if not url:
        return jsonify(error="Paste a link first."), 400
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url

    opts = base_opts() | {"skip_download": True}
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            data = ydl.extract_info(url, download=False)
    except Exception as e:
        return jsonify(error=clean_error(e)), 422

    if data.get("_type") == "playlist":
        entries = [e for e in (data.get("entries") or []) if e]
        if not entries:
            return jsonify(error="Playlists aren't supported. Paste a single video link."), 422
        data = entries[0]

    formats = data.get("formats") or []
    audio_sizes = [fmt_size(f) for f in formats
                   if f.get("vcodec") == "none" and f.get("acodec") not in (None, "none")]
    best_audio = max(audio_sizes, default=0)

    by_height = {}
    for f in formats:
        h = f.get("height")
        if not h or f.get("vcodec") in (None, "none"):
            continue
        if not HAS_FFMPEG and f.get("acodec") in (None, "none"):
            continue
        s = fmt_size(f)
        has_audio = f.get("acodec") not in (None, "none")
        cur = by_height.get(h)
        if cur is None or s > cur["size"]:
            by_height[h] = {"size": s, "fps": f.get("fps"), "has_audio": has_audio}

    options = []
    for h in sorted(by_height, reverse=True):
        v = by_height[h]
        total = v["size"] + (0 if v["has_audio"] else best_audio) if v["size"] else None
        fps = int(v["fps"]) if v.get("fps") and v["fps"] > 30 else None
        options.append({
            "value": str(h),
            "label": f"{h}p" + (str(fps) if fps else ""),
            "tag": quality_label(h),
            "ext": "MP4",
            "size": total,
            "too_big": bool(total and total > MAX_FILESIZE),
        })

    if not options:
        options.append({"value": "best", "label": "Best", "tag": "Original", "ext": "Auto",
                        "size": None, "too_big": False})

    if audio_sizes or data.get("acodec") not in (None, "none"):
        options.append({
            "value": "audio", "label": "Audio", "tag": "Audio only",
            "ext": "MP3" if HAS_FFMPEG else "M4A", "size": best_audio or None, "too_big": False,
        })

    return jsonify(
        title=data.get("title") or "Untitled",
        thumbnail=data.get("thumbnail"),
        duration=data.get("duration"),
        uploader=data.get("uploader") or data.get("channel") or data.get("extractor_key"),
        site=data.get("extractor_key"),
        url=data.get("webpage_url") or url,
        options=options,
        ffmpeg=HAS_FFMPEG,
    )


@app.route("/api/download", methods=["POST", "OPTIONS"])
def download():
    if request.method == "OPTIONS":
        return "", 204
    body = request.get_json(silent=True) or {}
    url = (body.get("url") or "").strip()
    quality = str(body.get("quality") or "best")
    if not url:
        return jsonify(error="Missing link."), 400
    if quality not in ("audio", "best") and not quality.isdigit():
        return jsonify(error="Invalid quality."), 400

    with LOCK:
        if active_jobs() >= MAX_JOBS:
            return jsonify(error="The server is busy right now. Try again in a minute."), 429
        job_id = uuid.uuid4().hex
        JOBS[job_id] = {
            "status": "queued", "progress": 0, "phase": "Starting…",
            "speed": None, "eta": None, "file": None, "error": None,
            "dir": tempfile.mkdtemp(prefix="streamgrabber_"), "created": time.time(),
        }
    threading.Thread(target=run_job, args=(job_id, url, quality), daemon=True).start()
    return jsonify(id=job_id)


def run_job(job_id, url, quality):
    job = JOBS[job_id]

    def hook(d):
        if d["status"] == "downloading":
            total = d.get("total_bytes") or d.get("total_bytes_estimate")
            done = d.get("downloaded_bytes") or 0
            info = d.get("info_dict") or {}
            kind = "audio" if info.get("vcodec") == "none" else "video"
            job.update(
                status="downloading",
                progress=round(done / total * 100, 1) if total else None,
                phase=f"Downloading {kind}",
                speed=d.get("speed"), eta=d.get("eta"),
            )
        elif d["status"] == "finished":
            job.update(progress=100, phase="Finishing up…", speed=None, eta=None)

    def pp_hook(d):
        if d["status"] == "started":
            job.update(status="processing", phase="Merging & converting…", progress=None)

    opts = base_opts() | {
        "outtmpl": os.path.join(job["dir"], "%(title).80s.%(ext)s"),
        "progress_hooks": [hook],
        "postprocessor_hooks": [pp_hook],
        "max_filesize": MAX_FILESIZE,
    }

    if quality == "audio":
        opts["format"] = "bestaudio[ext=m4a]/bestaudio/best"
        if HAS_FFMPEG:
            opts["postprocessors"] = [{
                "key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": "192",
            }]
    elif quality == "best":
        opts["format"] = "bv*+ba/b" if HAS_FFMPEG else "b"
    else:
        h = int(quality)
        if HAS_FFMPEG:
            opts["format"] = (f"bv*[height<={h}][ext=mp4]+ba[ext=m4a]/"
                              f"bv*[height<={h}]+ba/b[height<={h}]/b")
        else:
            opts["format"] = f"b[height<={h}]/b"

    if HAS_FFMPEG and quality != "audio":
        opts["merge_output_format"] = "mp4"

    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            ydl.download([url])
        files = [os.path.join(job["dir"], f) for f in os.listdir(job["dir"])
                 if not f.endswith((".part", ".ytdl", ".temp"))]
        if not files:
            raise RuntimeError("Download finished but no file was produced (it may be too large).")
        job.update(status="done", progress=100, phase="Ready",
                   file=max(files, key=os.path.getsize))
    except Exception as e:
        job.update(status="error", error=clean_error(e))


@app.get("/api/progress/<job_id>")
def progress(job_id):
    job = JOBS.get(job_id)
    if not job:
        return jsonify(status="error", error="This download expired. Start it again."), 404
    return jsonify(
        status=job["status"], progress=job["progress"], phase=job["phase"],
        speed=job["speed"], eta=job["eta"], error=job["error"],
        filename=os.path.basename(job["file"]) if job["file"] else None,
    )


@app.get("/api/file/<job_id>")
def get_file(job_id):
    job = JOBS.get(job_id)
    if not job or job["status"] != "done" or not job["file"]:
        abort(404)
    resp = send_file(job["file"], as_attachment=True,
                     download_name=os.path.basename(job["file"]))

    def cleanup():
        with LOCK:
            j = JOBS.pop(job_id, None)
        if j:
            shutil.rmtree(j["dir"], ignore_errors=True)

    resp.call_on_close(cleanup)
    return resp


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    if not HAS_FFMPEG:
        print("⚠  ffmpeg not found. HD video and MP3 need it; lower qualities still work.")
    print(f"▶  StreamGrabber running at http://127.0.0.1:{port}")
    print("   On your phone (same Wi-Fi): http://<this computer's IP>:" + str(port))
    app.run(host="0.0.0.0", port=port, threaded=True)
