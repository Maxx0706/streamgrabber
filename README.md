# StreamGrabber

A video downloader website for YouTube, Vimeo, X, TikTok, Instagram and 1,000+ other sites (powered by yt-dlp).

## Files

| File | What it is |
|---|---|
| `app.py` | The server that finds qualities and downloads videos |
| `index.html` | The website |
| `requirements.txt` | Python packages |
| `Dockerfile` | Recipe a cloud host uses to run it (includes ffmpeg) |
| `render.yaml` | One-click settings for Render |

---

## Option A: Put it online for free (Render)

No PC needed. You get a link like `https://streamgrabber.onrender.com` that works on any phone.

1. Make a free account at **github.com**.
2. Create a new repository (the **+** at the top right → *New repository*), name it `streamgrabber`, and click *Create*.
3. Click **"uploading an existing file"** and drag in all the files in this folder: `app.py`, `index.html`, `requirements.txt`, `Dockerfile`, `render.yaml`, `.dockerignore`. Click *Commit changes*.
4. Make a free account at **render.com** and sign in with GitHub.
5. Click **New → Blueprint**, pick your `streamgrabber` repository, and click *Apply*.
6. Wait about 5 minutes for the first build. Render shows your link when it's ready.

Share that link with anyone.

**Limits of the free plan:**
- It goes to sleep after 15 minutes with no visitors. The next visit takes about a minute to wake it up.
- Only 5 GB of transfer per month is included. That's roughly 30–50 videos at 720p. After that Render charges per GB.
- YouTube often blocks downloads from cloud servers ("confirm you're not a bot"). Most other sites work fine. See *YouTube problems* below.

## Option B: Lots of free bandwidth (Oracle Cloud)

Oracle's Always Free tier gives you a small server that never sleeps and comes with much more monthly transfer than Render. Setup takes about 30 minutes and is more technical:

1. Sign up at **cloud.oracle.com** (a card is needed for verification; the Always Free resources aren't charged).
2. Create an **Ubuntu** compute instance on an Always Free shape and open port 80 in its network security list.
3. SSH in and run:
   ```
   sudo apt update && sudo apt install -y docker.io git
   git clone https://github.com/YOUR-NAME/streamgrabber && cd streamgrabber
   sudo docker build -t streamgrabber .
   sudo docker run -d --restart always -p 80:8080 streamgrabber
   ```
4. Visit `http://YOUR-SERVER-IP` from any phone.

## Option C: Just your phone, at home

Run it on your PC and use it from your phone on the same Wi-Fi:

1. `pip install -r requirements.txt`, then `python app.py`
2. Find your PC's IP address (Windows: run `ipconfig`, look for *IPv4 Address*, like `192.168.1.23`).
3. On your phone open `http://192.168.1.23:5000`. Windows may ask to allow Python through the firewall; click *Allow*.

---

## Install it like an app on your phone

Open your link on the phone, then:
- **iPhone (Safari):** Share button → *Add to Home Screen*
- **Android (Chrome):** ⋮ menu → *Add to Home screen*

## YouTube problems

YouTube blocks many cloud servers. If you see "YouTube is blocking this server":
- Wait and try later, or use a different host (some IPs are blocked less).
- Advanced: export a `cookies.txt` from a **spare** YouTube account with a browser extension, add it to the server, and set the environment variable `YTDLP_COOKIES=/app/cookies.txt`. Don't use your main account; it can get flagged.

## Settings

Set these as environment variables on your host:

| Variable | Default | Meaning |
|---|---|---|
| `MAX_JOBS` | 3 | Downloads allowed at the same time |
| `MAX_FILESIZE_MB` | 1500 | Largest file the server will download |
| `YTDLP_COOKIES` | none | Path to a cookies.txt file |

## Common errors

- **"Start app.py, then open http://127.0.0.1:5000"**: you double-clicked `index.html`. Open the address in your browser instead.
- **Downloads stop working for a site**: sites change often. Rebuild/redeploy so it installs the newest yt-dlp, or run `pip install -U yt-dlp` locally.

Only download videos you have the right to save. Running a public downloader may break some sites' terms of service.
