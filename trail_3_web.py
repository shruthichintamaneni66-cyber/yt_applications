"""
YouTube Downloader - local web app (Flask + yt-dlp)

Setup (once):
    pip install flask yt-dlp
    (ffmpeg must be installed and on PATH for mp4 merging and mp3 conversion)

Run:
    python trail_3_web.py
    python trail_3_web.py --dir "D:\\Videos" --port 8080

Then open http://127.0.0.1:5000 in your browser.
The server only listens on your own computer (127.0.0.1).
"""

import argparse
import hmac
import html
import os
import re
import threading
import uuid
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, Response, jsonify, request
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

DEFAULT_DIR = Path(r"C:\Users\karth\OneDrive\Desktop\youtube downloads")
ALLOWED_QUALITIES = {360, 480, 720, 1080, 1440, 2160, 4320}

app = Flask(__name__)
app.config["MAX_CONTENT_LENGTH"] = 16 * 1024
SAVE_DIR = Path(os.environ.get("DOWNLOAD_DIR", str(DEFAULT_DIR)))
JOBS: dict[str, dict] = {}
LOCK = threading.Lock()
SLOTS = threading.Semaphore(2)  # at most 2 downloads at the same time
ANSI = re.compile(r"\x1b\[[0-9;]*m")


@app.before_request
def require_deployment_login():
    password = os.environ.get("DEPLOY_PASSWORD")
    if not password:
        if os.environ.get("REQUIRE_AUTH") == "1":
            return "Server authentication is not configured.", 503
        return None

    username = os.environ.get("DEPLOY_USERNAME", "downloader")
    credentials = request.authorization
    if (credentials is None or credentials.type.lower() != "basic"
            or not hmac.compare_digest(credentials.username or "", username)
            or not hmac.compare_digest(credentials.password or "", password)):
        return Response("Authentication required.", 401,
                        {"WWW-Authenticate": 'Basic realm="YouTube Downloader"'})
    return None


# ---------------------------------------------------------------- downloading
def build_options(job: dict) -> dict:
    if job["playlist"]:
        template = "%(playlist_title)s/%(playlist_index)02d - %(title)s.%(ext)s"
    else:
        template = "%(title)s.%(ext)s"

    def progress_hook(d: dict) -> None:
        info = d.get("info_dict") or {}
        total = d.get("total_bytes") or d.get("total_bytes_estimate")
        with LOCK:
            job["title"] = info.get("title") or job["title"]
            if info.get("n_entries"):
                job["item"] = info.get("playlist_index")
                job["items"] = info.get("n_entries")
            if d["status"] == "downloading":
                job["status"] = "downloading"
                if total:
                    job["percent"] = round(d.get("downloaded_bytes", 0) / total * 100, 1)
                job["speed"] = d.get("speed")
                job["eta"] = d.get("eta")
            elif d["status"] == "finished":
                job["percent"] = 100.0
                job["speed"] = None
                job["eta"] = None

    def postprocessor_hook(d: dict) -> None:
        if d["status"] == "started":
            with LOCK:
                job["status"] = "processing"

    opts = {
        "outtmpl": str(SAVE_DIR / template),
        "noplaylist": not job["playlist"],  # "&list=RD..." radio mixes are ignored unless asked for
        "windowsfilenames": True,
        "retries": 5,
        "fragment_retries": 5,
        "ignoreerrors": job["playlist"],
        "progress_hooks": [progress_hook],
        "postprocessor_hooks": [postprocessor_hook],
        "noprogress": True,
        "quiet": True,
        "no_warnings": True,
        "color": "no_color",
    }

    if job["mode"] == "audio":
        opts["format"] = "bestaudio/best"
        opts["postprocessors"] = [{
            "key": "FFmpegExtractAudio",
            "preferredcodec": "mp3",
            "preferredquality": "192",
        }]
    else:
        q = job["quality"]
        height = f"[height<={q}]" if q else ""
        opts["format"] = f"bestvideo{height}+bestaudio/best{height}"
        opts["merge_output_format"] = "mp4"

    return opts


def run_job(job: dict) -> None:
    with SLOTS:
        with LOCK:
            job["status"] = "downloading"
        error = None
        try:
            SAVE_DIR.mkdir(parents=True, exist_ok=True)
            with YoutubeDL(build_options(job)) as ydl:
                ydl.extract_info(job["url"], download=True)
        except DownloadError as e:
            error = ANSI.sub("", str(e))
        except Exception as e:  # network drops, missing ffmpeg, etc.
            error = ANSI.sub("", str(e))

        with LOCK:
            if error:
                job["status"] = "error"
                job["error"] = error
            else:
                job["status"] = "done"
                job["percent"] = 100.0
            job["speed"] = None
            job["eta"] = None


# ---------------------------------------------------------------------- API
@app.post("/api/download")
def api_download():
    data = request.get_json(silent=True) or {}
    if not isinstance(data, dict):
        return jsonify(error="Please send a video URL and download options."), 400
    raw_url = data.get("url")
    url = raw_url.strip() if isinstance(raw_url, str) else ""
    parsed = None
    try:
        parsed = urlsplit(url)
        hostname = (parsed.hostname or "").lower().rstrip(".")
    except ValueError:
        hostname = ""
    allowed_hosts = {"youtube.com", "www.youtube.com", "m.youtube.com",
                     "music.youtube.com", "youtu.be", "www.youtu.be"}
    if (parsed is None or parsed.scheme.lower() not in {"http", "https"} or hostname not in allowed_hosts
            or parsed.username or parsed.password):
        return jsonify(error="Please enter a valid YouTube video or playlist URL."), 400

    mode = "audio" if data.get("mode") == "audio" else "video"
    quality = data.get("quality")
    quality = int(quality) if str(quality).isdigit() and int(quality) in ALLOWED_QUALITIES else None

    job = {
        "id": uuid.uuid4().hex[:8],
        "url": url,
        "title": url,
        "mode": mode,
        "quality": quality,
        "playlist": data.get("playlist") is True,
        "status": "queued",
        "percent": 0.0,
        "speed": None,
        "eta": None,
        "item": None,
        "items": None,
        "error": None,
    }
    with LOCK:
        JOBS[job["id"]] = job
    threading.Thread(target=run_job, args=(job,), daemon=True).start()
    return jsonify(id=job["id"]), 201


@app.get("/api/jobs")
def api_jobs():
    with LOCK:
        return jsonify(list(reversed([dict(j) for j in JOBS.values()])))


@app.post("/api/clear")
def api_clear():
    with LOCK:
        for jid in [k for k, j in JOBS.items() if j["status"] in ("done", "error")]:
            del JOBS[jid]
    return jsonify(ok=True)


@app.get("/")
def index():
    return Response(INDEX_HTML.replace("__SAVE_DIR__", html.escape(str(SAVE_DIR))),
                    mimetype="text/html")


# --------------------------------------------------------------------- page
INDEX_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>YouTube Downloader</title>
<style>
  :root {
    --bg: #f6f6f4; --card: #ffffff; --text: #1c1c1a; --muted: #6b6b66;
    --border: #e2e2dd; --accent: #d93025; --accent-text: #ffffff;
    --track: #ececE7; --ok: #1e8e3e; --err: #c5221f;
  }
  @media (prefers-color-scheme: dark) {
    :root {
      --bg: #151514; --card: #1f1f1d; --text: #ecece8; --muted: #9a9a93;
      --border: #333330; --accent: #ff5a4f; --accent-text: #1a1a18;
      --track: #333330; --ok: #5bd17a; --err: #ff7b72;
    }
  }
  * { box-sizing: border-box; }
  body {
    margin: 0; background: var(--bg); color: var(--text);
    font-family: system-ui, -apple-system, "Segoe UI", Roboto, sans-serif;
    line-height: 1.5;
  }
  main { max-width: 720px; margin: 0 auto; padding: 32px 16px 64px; }
  h1 { font-size: 1.5rem; margin: 0 0 4px; }
  .sub { color: var(--muted); margin: 0 0 24px; font-size: .9rem; word-break: break-all; }
  .card {
    background: var(--card); border: 1px solid var(--border);
    border-radius: 12px; padding: 20px; margin-bottom: 24px;
  }
  label { display: block; font-size: .85rem; color: var(--muted); margin-bottom: 6px; }
  input[type=url], select {
    width: 100%; padding: 10px 12px; font: inherit; color: var(--text);
    background: var(--bg); border: 1px solid var(--border); border-radius: 8px;
  }
  .row { display: flex; gap: 16px; flex-wrap: wrap; margin-top: 16px; align-items: flex-end; }
  .row > div { flex: 1 1 160px; }
  .seg { display: flex; border: 1px solid var(--border); border-radius: 8px; overflow: hidden; }
  .seg button {
    flex: 1; padding: 10px; font: inherit; border: 0; cursor: pointer;
    background: var(--bg); color: var(--muted);
  }
  .seg button.on { background: var(--text); color: var(--bg); }
  .check { display: flex; gap: 8px; align-items: center; margin-top: 16px; font-size: .9rem; }
  .check input { width: 16px; height: 16px; }
  .go {
    margin-top: 20px; width: 100%; padding: 12px; font: inherit; font-weight: 600;
    border: 0; border-radius: 8px; cursor: pointer;
    background: var(--accent); color: var(--accent-text);
  }
  .go:disabled { opacity: .6; cursor: default; }
  #msg { color: var(--err); font-size: .85rem; margin-top: 10px; min-height: 1.2em; }
  .head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 12px; }
  .head h2 { font-size: 1rem; margin: 0; }
  .link { background: none; border: 0; color: var(--muted); cursor: pointer; font: inherit; font-size: .85rem; }
  .job { border-top: 1px solid var(--border); padding: 14px 0; }
  .job:first-child { border-top: 0; padding-top: 0; }
  .title { font-weight: 600; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .meta { color: var(--muted); font-size: .8rem; margin-top: 2px; }
  .bar { height: 6px; background: var(--track); border-radius: 3px; margin-top: 8px; overflow: hidden; }
  .bar > span { display: block; height: 100%; background: var(--accent); transition: width .3s; }
  .done .bar > span { background: var(--ok); }
  .error .bar > span { background: var(--err); }
  .err { color: var(--err); font-size: .8rem; margin-top: 6px; word-break: break-word; }
  .empty { color: var(--muted); font-size: .9rem; }
</style>
</head>
<body>
<main>
  <h1>YouTube Downloader</h1>
  <p class="sub">Saving to: __SAVE_DIR__</p>

  <section class="card">
    <label for="url">Video or playlist URL</label>
    <input id="url" type="url" placeholder="https://www.youtube.com/watch?v=..." autofocus>

    <div class="row">
      <div>
        <label>Format</label>
        <div class="seg">
          <button type="button" id="m-video" class="on">Video (mp4)</button>
          <button type="button" id="m-audio">Audio (mp3)</button>
        </div>
      </div>
      <div id="qwrap">
        <label for="quality">Max quality</label>
        <select id="quality">
          <option value="">Best available</option>
          <option value="4320">4320p (8K)</option>
          <option value="2160">2160p (4K)</option>
          <option value="1440">1440p</option>
          <option value="1080">1080p</option>
          <option value="720">720p</option>
          <option value="480">480p</option>
          <option value="360">360p</option>
        </select>
      </div>
    </div>

    <label class="check">
      <input id="playlist" type="checkbox"> Download the whole playlist (not just this video)
    </label>

    <button class="go" id="go">Download</button>
    <div id="msg"></div>
  </section>

  <section class="card">
    <div class="head">
      <h2>Downloads</h2>
      <button class="link" id="clear">Clear finished</button>
    </div>
    <div id="jobs"><p class="empty">Nothing yet.</p></div>
  </section>
</main>

<script>
  let mode = "video";
  const $ = id => document.getElementById(id);

  function setMode(m) {
    mode = m;
    $("m-video").classList.toggle("on", m === "video");
    $("m-audio").classList.toggle("on", m === "audio");
    $("qwrap").style.display = m === "video" ? "" : "none";
  }
  $("m-video").onclick = () => setMode("video");
  $("m-audio").onclick = () => setMode("audio");

  async function start() {
    const url = $("url").value.trim();
    $("msg").textContent = "";
    if (!url) { $("msg").textContent = "Paste a URL first."; return; }
    $("go").disabled = true;
    try {
      const res = await fetch("/api/download", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url, mode,
          quality: $("quality").value,
          playlist: $("playlist").checked
        })
      });
      const data = await res.json();
      if (!res.ok) { $("msg").textContent = data.error || "Something went wrong."; }
      else { $("url").value = ""; refresh(); }
    } catch (e) {
      $("msg").textContent = "Could not reach the server.";
    }
    $("go").disabled = false;
  }
  $("go").onclick = start;
  $("url").addEventListener("keydown", e => { if (e.key === "Enter") start(); });
  $("clear").onclick = async () => { await fetch("/api/clear", { method: "POST" }); refresh(); };

  function fmtSpeed(b) { return b ? (b / 1048576).toFixed(1) + " MB/s" : ""; }
  function fmtEta(s) {
    if (s == null) return "";
    return "ETA " + Math.floor(s / 60) + ":" + String(Math.floor(s % 60)).padStart(2, "0");
  }

  function describe(j) {
    const parts = [];
    parts.push({ queued: "Queued", downloading: "Downloading", processing: "Converting / merging",
                 done: "Finished", error: "Failed" }[j.status]);
    if (j.status === "downloading") {
      parts.push(j.percent.toFixed(0) + "%");
      if (j.speed) parts.push(fmtSpeed(j.speed));
      if (j.eta != null) parts.push(fmtEta(j.eta));
    }
    if (j.items) parts.push("item " + j.item + " of " + j.items);
    parts.push(j.mode === "audio" ? "mp3" : (j.quality ? "up to " + j.quality + "p" : "best quality"));
    return parts.join(" \u00b7 ");
  }

  function render(jobs) {
    const box = $("jobs");
    box.textContent = "";
    if (!jobs.length) {
      const p = document.createElement("p");
      p.className = "empty"; p.textContent = "Nothing yet.";
      box.appendChild(p);
      return;
    }
    for (const j of jobs) {
      const el = document.createElement("div");
      el.className = "job " + j.status;

      const t = document.createElement("div");
      t.className = "title"; t.textContent = j.title; t.title = j.title;

      const m = document.createElement("div");
      m.className = "meta"; m.textContent = describe(j);

      const bar = document.createElement("div");
      bar.className = "bar";
      const fill = document.createElement("span");
      fill.style.width = (j.status === "error" ? 100 : j.percent) + "%";
      bar.appendChild(fill);

      el.append(t, m, bar);
      if (j.error) {
        const e = document.createElement("div");
        e.className = "err"; e.textContent = j.error;
        el.appendChild(e);
      }
      box.appendChild(el);
    }
  }

  async function refresh() {
    try {
      const res = await fetch("/api/jobs");
      render(await res.json());
    } catch (e) { /* server briefly unavailable; try again next tick */ }
  }
  setInterval(refresh, 1000);
  refresh();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------- main
def main() -> None:
    global SAVE_DIR
    parser = argparse.ArgumentParser(description="Local YouTube downloader web app.")
    parser.add_argument("--dir", type=Path, default=SAVE_DIR, help="Folder to save downloads in")
    parser.add_argument("--port", type=int, default=int(os.environ.get("PORT", "5000")), help="Port to listen on")
    args = parser.parse_args()

    SAVE_DIR = args.dir
    SAVE_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Saving to: {SAVE_DIR}")
    host = os.environ.get("HOST", "127.0.0.1")
    print(f"Open http://{host}:{args.port} in your browser (Ctrl+C to stop)")
    app.run(host=host, port=args.port, threaded=True)


if __name__ == "__main__":
    main()
