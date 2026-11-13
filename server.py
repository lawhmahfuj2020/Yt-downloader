import os, re, shutil, tempfile, glob
from flask import Flask, request, jsonify, send_file, abort, after_this_request
import yt_dlp

app = Flask(__name__)
KEY = os.environ.get("API_KEY", "")
COOKIES = os.environ.get("COOKIES_FILE", "")  # optional: path to cookies.txt
HEIGHTS = [1080, 720, 480, 360]
ABR = [320, 192, 128, 64]


def auth():
    if KEY and request.args.get("key") != KEY and request.headers.get("X-Key") != KEY:
        abort(401)


def opts(extra=None):
    o = {"quiet": True, "no_warnings": True, "noplaylist": True}
    if COOKIES and os.path.exists(COOKIES):
        o["cookiefile"] = COOKIES
    o.update(extra or {})
    return o


def resolve(url, q):
    if url:
        return url
    if not q:
        abort(400)
    with yt_dlp.YoutubeDL(opts({"extract_flat": True})) as y:
        r = y.extract_info("ytsearch1:" + q, download=False)
    e = (r.get("entries") or [None])[0]
    if not e:
        abort(404)
    return e.get("url") or "https://www.youtube.com/watch?v=" + e["id"]


GOOD = ["official audio", "official video", "official music video", "lyrics", "lyric video",
        "audio", "ft.", "feat.", "remix", "acoustic", "(live", "soundtrack", "ost", "full song"]
BAD = ["podcast", "interview", "vlog", "tutorial", "how to", "full episode", "episode", "reaction",
       "review", "news", "highlights", "gameplay", "walkthrough", "lecture", "sermon", "speech",
       "trailer", "unboxing", "explained", "documentary", "stream", "live stream", "compilation of"]


def song_score(d, app=""):
    t = ((d.get("title") or "") + " " + (d.get("channel") or "")).lower()
    dur = d.get("duration") or 0
    cats = [c.lower() for c in (d.get("categories") or [])]
    sc = 0
    if app == "ytmusic":
        sc += 4
    if "music" in cats:
        sc += 3
    if d.get("track") or d.get("artist"):
        sc += 3
    if (d.get("channel") or "").endswith(" - Topic"):
        sc += 3
    if any(g in t for g in GOOD):
        sc += 2
    if any(b in t for b in BAD):
        sc -= 3
    if 90 <= dur <= 600:
        sc += 2
    elif 600 < dur <= 900:
        sc -= 1
    elif dur > 900:
        sc -= 3
    elif 0 < dur < 60:
        sc -= 2
    if cats and "music" not in cats and "entertainment" not in cats:
        sc -= 2
    v = "song" if sc >= 4 else ("not_song" if sc <= 0 else "unsure")
    return {"score": sc, "verdict": v}


@app.get("/")
def home():
    return "ok"


@app.get("/info")
def info():
    auth()
    url = resolve(request.args.get("url"), request.args.get("q"))
    try:
        with yt_dlp.YoutubeDL(opts()) as y:
            d = y.extract_info(url, download=False)
    except Exception as e:
        return jsonify(error=str(e)[:200]), 502
    fm = d.get("formats", [])
    audio = max([f.get("filesize") or f.get("filesize_approx") or 0
                 for f in fm if f.get("vcodec") == "none"] or [0])
    video = []
    for h in HEIGHTS:
        s = [f.get("filesize") or f.get("filesize_approx") or 0
             for f in fm if f.get("vcodec") not in (None, "none") and f.get("height") == h]
        if s:
            video.append({"height": h, "mb": round((max(s) + audio) / 1048576)})
    dur = d.get("duration") or 0
    mp3 = [{"abr": a, "mb": round(dur * a * 125 / 1048576)} for a in ABR]
    return jsonify(song=song_score(d, request.args.get("app", "")),
                   url=d.get("webpage_url", url), title=d.get("title"),
                   channel=d.get("channel"), duration=dur,
                   thumb=d.get("thumbnail"), video=video, audio=mp3)


@app.get("/download")
def download():
    auth()
    url = request.args.get("url") or abort(400)
    kind = request.args.get("type", "video")
    tmp = tempfile.mkdtemp()
    o = opts({"outtmpl": tmp + "/%(title).80s.%(ext)s"})
    if kind == "audio":
        abr = request.args.get("abr", "best")
        abr = "0" if abr == "best" else abr  # 0 = highest MP3 quality
        o.update(format="bestaudio/best", postprocessors=[{
            "key": "FFmpegExtractAudio", "preferredcodec": "mp3", "preferredquality": abr}])
    else:
        h = int(request.args.get("height", 720))
        o.update(format=f"bv*[height<={h}]+ba/b[height<={h}]", merge_output_format="mp4")

    @after_this_request
    def cleanup(resp):
        shutil.rmtree(tmp, ignore_errors=True)
        return resp

    try:
        with yt_dlp.YoutubeDL(o) as y:
            y.download([url])
    except Exception as e:
        shutil.rmtree(tmp, ignore_errors=True)
        return jsonify(error=str(e)[:200]), 502
    f = (glob.glob(tmp + "/*") or abort(500))[0]
    return send_file(f, as_attachment=True, download_name=re.sub(r'[\\/:*?"<>|]', "", os.path.basename(f)))
