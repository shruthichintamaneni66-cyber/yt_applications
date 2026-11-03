# YouTube Downloader Website

A Flask website for downloading YouTube videos up to 4K, MP3 audio, and playlists. It can run on your Windows computer or as a password-protected Docker service on a Linux server.

## Run on Windows

1. Install Python 3 for Windows.
2. Double-click `start_website.bat` to install Flask and yt-dlp and start the website.
3. Open [http://127.0.0.1:5000](http://127.0.0.1:5000).

Keep the command window open while using the site. Install FFmpeg and add it to PATH for MP4 merging and MP3 conversion. The default download folder is `C:\Users\karth\OneDrive\Desktop\youtube downloads`.

## Deploy on a Linux server

Install Docker Engine and the Docker Compose plugin on a Linux server. Copy this folder to the server, then:

1. Copy `.env.example` to `.env` and set `DEPLOY_PASSWORD` to a long, unique password. Keep `.env` private.
2. Start the service with `docker compose up -d --build`.
3. Put a TLS reverse proxy in front of it. The app listens on `127.0.0.1:8080` on the server by default, so the port is not exposed to the public network.
4. Point a domain with DNS to the server and configure the proxy to forward HTTPS traffic to `127.0.0.1:8080`.
5. Visit your HTTPS domain. The browser will request the username and password from `.env`.

For example, with Caddy installed on the server, add a site block like this to its Caddyfile, replacing the sample domain:

```text
downloader.example.com {
    reverse_proxy 127.0.0.1:8080
}
```

Caddy can obtain and renew a TLS certificate when the domain points to the server and ports 80 and 443 are reachable. Do not expose the app over plain HTTP on the public internet because browser basic authentication is only protected in transit by HTTPS.

The Docker volume named `downloads` keeps downloaded files across container rebuilds and restarts. Download job status is held in memory and resets if the container restarts. Run one app instance; this version does not share job state across multiple replicas. To stop it, run `docker compose down`. The named download volume remains unless explicitly removed.

## Local command options

You can choose a download folder and port when running directly:

```text
py trail_3_web.py --dir "D:\Videos" --port 8080
```
