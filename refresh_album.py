import os, io, json, base64, subprocess, sys, hashlib
from datetime import datetime, timezone, timedelta
try:
    from zoneinfo import ZoneInfo
    _TZ = ZoneInfo("America/Chicago")
except Exception:
    _TZ = timezone(timedelta(hours=-5))

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import exifread
import pillow_heif
from PIL import Image, ImageOps
import urllib.request, urllib.parse, time

DRIVE_FOLDER_ID = "1idR48mqlXyY1Jjh8E0ZxpnG2N5fqUhUN"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
KEY_PATH = os.environ.get("SERVICE_ACCOUNT_KEY_PATH", "sa_key.json")
CACHE_PATH = "geocode_cache.json"
VOTERS_CACHE_PATH = "voters_cache.json"

TRIP_START = "2026-09-10"
PRE_TRIP_WINDOW_START = "2026-08-11"

DAYS = [
  {"key":"PRE","roman":"\u2014","title":"Before We Go: Packing & Planning","railTitle":"Before we go","date":"Aug 11\u2013Sept 9, 2026","short":"Aug 11\u2013Sept 9","dates":[]},
  {"key":"01","roman":"I","title":"The Distance Between Here and There","railTitle":"The distance here to there","date":"Thu\u2013Fri, Sept 10\u201311, 2026","short":"Sept 10\u201311","dates":["2026-09-10","2026-09-11"]},
  {"key":"02","roman":"II","title":"Kealey's Birthday in London","railTitle":"Kealey's birthday","date":"Saturday, Sept 12, 2026","short":"Sept 12","dates":["2026-09-12"]},
  {"key":"03","roman":"III","title":"Westminster and the Thames","railTitle":"Westminster","date":"Sunday, Sept 13, 2026","short":"Sept 13","dates":["2026-09-13"]},
  {"key":"04","roman":"IV","title":"North to Scotland","railTitle":"North to Scotland","date":"Monday, Sept 14, 2026","short":"Sept 14","dates":["2026-09-14"]},
  {"key":"05","roman":"V","title":"Gullane and the Road to St Andrews","railTitle":"Gullane","date":"Tuesday, Sept 15, 2026","short":"Sept 15","dates":["2026-09-15"]},
  {"key":"06","roman":"VI","title":"St Andrews and Edinburgh","railTitle":"St Andrews","date":"Wednesday, Sept 16, 2026","short":"Sept 16","dates":["2026-09-16"]},
  {"key":"07","roman":"VII","title":"London's South Bank","railTitle":"South Bank","date":"Thursday, Sept 17, 2026","short":"Sept 17","dates":["2026-09-17"]},
  {"key":"08","roman":"VIII","title":"On to Wembley","railTitle":"On to Wembley","date":"Friday, Sept 18, 2026","short":"Sept 18","dates":["2026-09-18"]},
  {"key":"09","roman":"IX","title":"Game Day at Wembley","railTitle":"Game day","date":"Saturday, Sept 19, 2026","short":"Sept 19","dates":["2026-09-19"]},
  {"key":"10","roman":"X","title":"London to Phoenix","railTitle":"London to Phoenix","date":"Sunday, Sept 20, 2026","short":"Sept 20","dates":["2026-09-20"]},
  {"key":"11","roman":"XI","title":"The Road Home","railTitle":"The road home","date":"Monday, Sept 21, 2026","short":"Sept 21","dates":["2026-09-21"]},
  {"key":"00","roman":"?","title":"Unsorted","railTitle":"Unsorted","date":"No date or GPS data found","short":"\u2014","dates":[]},
]
# Wembley game-weekend days get ASU maroon/gold styling so they visually jump out from the rest
ASU_DAYS = {"08", "09"}
# The rest of the days quietly match the book's own three-color chapter system,
# so the album already feels visually consistent with the book once it's unveiled.
# These are brightened versions of the book's chapter colors (navy/burgundy/sage) —
# the book uses the darker originals as background panels with light text on top,
# but here they're used as text color on a dark page background, so the darker
# originals (especially navy) are nearly invisible. These are lightened just
# enough to read clearly while still reading as "navy," "burgundy," "sage."
DAY_ACCENTS = {
  "01": "#7ea6d9",  # navy (brightened for legibility on dark bg)
  "02": "#d98a95",  # burgundy (brightened)
  "03": "#7ea6d9",  # navy (brightened)
  "04": "#8fbf87",  # sage (brightened)
  "05": "#8fbf87",  # sage (brightened)
  "06": "#8fbf87",  # sage (brightened)
  "07": "#d98a95",  # burgundy (brightened)
  "10": "#7ea6d9",  # navy (brightened)
  "11": "#8fbf87",  # sage (brightened)
}
TAGS = {
  "PRE":"Compression cubes, a Garmin charger, and mounting anticipation.",
  "01":"Omaha meets Phoenix at the gate, and by morning all four of us are stepping off the same plane in London.",
  "02":"Twenty-five years old, spent bending an entire city around her.",
  "03":"History old enough to make our problems feel small.",
  "04":"Hickory clubs are humbling, and Musselburgh doesn't care how good you think you are.",
  "05":"About as close to a golf pilgrimage as this family gets.",
  "06":"One more St Andrews morning before we let it go.",
  "07":"A market, a cathedral, and a wheel that shows you all of it at once.",
  "08":"The trip starts to feel like it's building toward something specific. Because it is.",
  "09":"The one we built the whole itinerary around.",
  "10":"Wheels up in London, somehow landing the same afternoon we left.",
  "11":"Eleven days none of us will forget, and a lot more story than we left with.",
  "00":"Usually means the photo was texted or AirDropped and lost its original info along the way.",
}
date_to_day = {}
for d in DAYS:
    for dt in d["dates"]:
        date_to_day[dt] = d["key"]

pillow_heif.register_heif_opener()

REACTIONS_ENDPOINT = os.environ.get("REACTIONS_ENDPOINT", "")

def fetch_reactions():
    if not REACTIONS_ENDPOINT:
        return {}
    try:
        req = urllib.request.Request(REACTIONS_ENDPOINT, headers={"User-Agent": "conrad-family-album/1.0"})
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read()
            print(f"fetch_reactions: HTTP {resp.status}, {len(body)} bytes", file=sys.stderr)
            return json.loads(body)
    except Exception as e:
        print(f"fetch_reactions FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return {}

def fetch_voters():
    # Returns { photoId: { "heart": ["Kayla", ...], "laugh": [...], "thumbsdown": [...] } }
    # Retries a few times before giving up, and falls back to the last known-good
    # result on disk if every attempt fails — a transient network hiccup to Google
    # should never make real votes disappear from the site.
    if not REACTIONS_ENDPOINT:
        return {}
    sep = "&" if "?" in REACTIONS_ENDPOINT else "?"
    url = f"{REACTIONS_ENDPOINT}{sep}mode=voters"
    last_error = None
    for attempt in range(1, 4):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "conrad-family-album/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read()
                print(f"fetch_voters attempt {attempt}: HTTP {resp.status}, {len(body)} bytes", file=sys.stderr)
                rows = json.loads(body)
            out = {}
            for row in rows:
                pid = row.get("photoId")
                reaction = row.get("reaction")
                voter = (row.get("voter") or "").strip()
                if not pid or not reaction or not voter:
                    continue
                out.setdefault(pid, {}).setdefault(reaction, [])
                if voter not in out[pid][reaction]:
                    out[pid][reaction].append(voter)
            print(f"fetch_voters: parsed {len(rows)} rows into {len(out)} photos", file=sys.stderr)
            try:
                json.dump(out, open(VOTERS_CACHE_PATH, "w"))
            except Exception:
                pass
            return out
        except Exception as e:
            last_error = e
            print(f"fetch_voters attempt {attempt} FAILED: {type(e).__name__}: {e}", file=sys.stderr)
            if attempt < 3:
                time.sleep(2)
    print(f"fetch_voters: all attempts failed ({last_error}); falling back to last known-good cache", file=sys.stderr)
    try:
        return json.load(open(VOTERS_CACHE_PATH))
    except Exception:
        print("fetch_voters: no cache available either, returning empty", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"fetch_voters FAILED: {type(e).__name__}: {e}", file=sys.stderr)
        return {}

def get_drive_service():
    creds = service_account.Credentials.from_service_account_file(KEY_PATH, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)

def list_photos(service):
    results = []
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{DRIVE_FOLDER_ID}' in parents and mimeType contains 'image/' and trashed=false",
            fields="nextPageToken, files(id, name, mimeType, imageMediaMetadata, modifiedTime)",
            pageToken=page_token, pageSize=1000
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results

def list_videos(service):
    """Same folder, video files only. Uses videoMediaMetadata (Drive's own
    parsed duration/dimensions) since exifread doesn't read video containers."""
    results = []
    page_token = None
    while True:
        resp = service.files().list(
            q=f"'{DRIVE_FOLDER_ID}' in parents and mimeType contains 'video/' and trashed=false",
            fields="nextPageToken, files(id, name, mimeType, videoMediaMetadata, modifiedTime)",
            pageToken=page_token, pageSize=1000
        ).execute()
        results.extend(resp.get("files", []))
        page_token = resp.get("nextPageToken")
        if not page_token:
            break
    return results

def download_file(service, file_id):
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return buf.read()

def extract_exif(raw_bytes, filename, drive_file=None):
    date_obj, lat, lon = None, None, None
    try:
        tags = exifread.process_file(io.BytesIO(raw_bytes), details=False)
        dt_tag = tags.get("EXIF DateTimeOriginal") or tags.get("Image DateTime")
        if dt_tag:
            date_obj = datetime.strptime(str(dt_tag), "%Y:%m:%d %H:%M:%S")
        def to_deg(vals, ref):
            d = float(vals.values[0].num) / float(vals.values[0].den)
            m = float(vals.values[1].num) / float(vals.values[1].den)
            s = float(vals.values[2].num) / float(vals.values[2].den)
            val = d + m/60 + s/3600
            if ref in ["S", "W"]:
                val = -val
            return val
        gps_lat = tags.get("GPS GPSLatitude")
        gps_lat_ref = tags.get("GPS GPSLatitudeRef")
        gps_lon = tags.get("GPS GPSLongitude")
        gps_lon_ref = tags.get("GPS GPSLongitudeRef")
        if gps_lat and gps_lon:
            lat = to_deg(gps_lat, str(gps_lat_ref))
            lon = to_deg(gps_lon, str(gps_lon_ref))
    except Exception:
        pass

    # Fallback 1: Drive's own parsed image metadata (more reliable for HEIC/iPhone photos)
    if drive_file:
        imm = drive_file.get("imageMediaMetadata", {}) or {}
        if date_obj is None:
            drive_time = imm.get("time")
            if drive_time:
                try:
                    date_obj = datetime.strptime(drive_time, "%Y:%m:%d %H:%M:%S")
                except Exception:
                    pass
        if lat is None:
            loc_meta = imm.get("location")
            if loc_meta and "latitude" in loc_meta and "longitude" in loc_meta:
                lat = loc_meta["latitude"]
                lon = loc_meta["longitude"]

    # Fallback 2: when the photo was uploaded to Drive (last resort, not the capture date)
    date_is_upload_only = False
    if date_obj is None and drive_file:
        modified = drive_file.get("modifiedTime")
        if modified:
            try:
                date_obj = datetime.strptime(modified[:19], "%Y-%m-%dT%H:%M:%S")
                date_is_upload_only = True
            except Exception:
                pass

    return date_obj, lat, lon, date_is_upload_only

def extract_video_date(filepath, drive_file):
    """Videos don't carry EXIF; pull creation_time from the container via
    ffprobe first (iPhone MOV/MP4 both set this), falling back to Drive's
    upload time like photos do."""
    date_obj, date_is_upload_only = None, False
    try:
        out = subprocess.run(
            ["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format", filepath],
            capture_output=True, text=True, timeout=30
        )
        meta = json.loads(out.stdout)
        creation_time = meta.get("format", {}).get("tags", {}).get("creation_time")
        if creation_time:
            date_obj = datetime.strptime(creation_time[:19], "%Y-%m-%dT%H:%M:%S")
    except Exception:
        pass
    if date_obj is None and drive_file:
        modified = drive_file.get("modifiedTime")
        if modified:
            try:
                date_obj = datetime.strptime(modified[:19], "%Y-%m-%dT%H:%M:%S")
                date_is_upload_only = True
            except Exception:
                pass
    return date_obj, date_is_upload_only

ALBUM_IMAGE_DIR = os.path.join("book-images", "album")
ALBUM_VIDEO_DIR = os.path.join("book-images", "album")
MAX_VIDEO_MB = 40  # keep well under GitHub's 100MB hard limit and 50MB warning

def to_web_video(raw_bytes, filename, video_id):
    """Writes the raw video to a temp file, re-encodes it to a size-capped
    web-friendly MP4 (H.264/AAC, capped bitrate), and extracts a poster
    frame at the 1-second mark for the gallery grid and lightbox."""
    os.makedirs(ALBUM_VIDEO_DIR, exist_ok=True)
    ext = os.path.splitext(filename)[1] or ".mov"
    tmp_in = f"/tmp/{video_id}_in{ext}"
    with open(tmp_in, "wb") as f:
        f.write(raw_bytes)

    video_name = f"{video_id}.mp4"
    video_path = os.path.join(ALBUM_VIDEO_DIR, video_name)
    poster_name = f"{video_id}_poster.jpg"
    poster_path = os.path.join(ALBUM_VIDEO_DIR, poster_name)

    try:
        # Re-encode: 720p cap, capped bitrate so a handful of clips doesn't
        # blow up repo size or page load; audio kept but light.
        subprocess.run([
            "ffmpeg", "-y", "-i", tmp_in,
            "-map_metadata", "0",
            "-vf", "scale='min(1280,iw)':-2",
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "26",
            "-maxrate", "2M", "-bufsize", "4M",
            "-c:a", "aac", "-b:a", "96k",
            "-movflags", "+faststart",
            video_path
        ], check=True, capture_output=True, timeout=600)

        size_mb = os.path.getsize(video_path) / (1024 * 1024)
        if size_mb > MAX_VIDEO_MB:
            print(f"WARNING: {filename} encoded to {size_mb:.1f}MB, over the {MAX_VIDEO_MB}MB cap — re-encoding smaller")
            subprocess.run([
                "ffmpeg", "-y", "-i", tmp_in,
                "-map_metadata", "0",
                "-vf", "scale='min(854,iw)':-2",
                "-c:v", "libx264", "-preset", "veryfast", "-crf", "30",
                "-maxrate", "1M", "-bufsize", "2M",
                "-c:a", "aac", "-b:a", "64k",
                "-movflags", "+faststart",
                video_path
            ], check=True, capture_output=True, timeout=600)

        # Poster frame at 1s (falls back to 0s for very short clips)
        subprocess.run([
            "ffmpeg", "-y", "-ss", "1", "-i", video_path, "-frames:v", "1", poster_path
        ], capture_output=True, timeout=60)
        if not os.path.exists(poster_path):
            subprocess.run([
                "ffmpeg", "-y", "-ss", "0", "-i", video_path, "-frames:v", "1", poster_path
            ], capture_output=True, timeout=60)

        # ffprobe path used by extract_video_date runs against the re-encoded file
        return f"book-images/album/{video_name}", f"book-images/album/{poster_name}", video_path
    except Exception as e:
        print(f"Video conversion FAILED for {filename} ({video_id}): {e}")
        return None, None, None
    finally:
        if os.path.exists(tmp_in):
            os.remove(tmp_in)

def compute_dhash(img):
    """Difference hash: a lightweight perceptual fingerprint (64 bits) used
    to catch near-duplicate burst/Live Photo frames that exact byte hashing
    can't, since each frame is a genuinely different file. Resizes to a tiny
    9x8 grayscale grid and encodes whether each pixel is brighter than its
    right neighbor -- visually similar images produce hashes that differ in
    only a handful of bits, even though the underlying bytes are unrelated."""
    small = img.convert("L").resize((9, 8), Image.LANCZOS)
    pixels = list(small.getdata())
    bits = 0
    for row in range(8):
        for col in range(8):
            bits <<= 1
            if pixels[row * 9 + col] > pixels[row * 9 + col + 1]:
                bits |= 1
    return bits

def hamming_distance(a, b):
    return bin(a ^ b).count("1")

def to_web_image(raw_bytes, filename, photo_id):
    """Resizes the photo and writes full/thumb JPEGs to book-images/album/,
    returning their relative paths plus a perceptual hash for burst-shot
    detection. Photos are served as real files rather than embedded as
    base64 so index.html stays a normal page size instead of growing by
    ~1-2MB per photo forever."""
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")

        phash = compute_dhash(img)

        os.makedirs(ALBUM_IMAGE_DIR, exist_ok=True)

        full = img.copy()
        full.thumbnail((1600, 1600))
        full_name = f"{photo_id}_full.jpg"
        full_path = os.path.join(ALBUM_IMAGE_DIR, full_name)
        full.save(full_path, format="JPEG", quality=82)

        thumb = img.copy()
        thumb.thumbnail((600, 600))
        thumb_name = f"{photo_id}_thumb.jpg"
        thumb_path = os.path.join(ALBUM_IMAGE_DIR, thumb_name)
        thumb.save(thumb_path, format="JPEG", quality=82)

        return (
            f"book-images/album/{full_name}",
            f"book-images/album/{thumb_name}",
            phash,
        )
    except Exception:
        return None, None, None

def reverse_geocode(lat, lon, cache):
    key = f"{lat:.3f},{lon:.3f}"
    if key in cache:
        return cache[key]
    try:
        url = f"https://nominatim.openstreetmap.org/reverse?format=json&lat={lat}&lon={lon}&zoom=16&addressdetails=1"
        req = urllib.request.Request(url, headers={"User-Agent": "conrad-family-album/1.0"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        addr = data.get("address", {})
        place = addr.get("attraction") or addr.get("tourism") or addr.get("building") or addr.get("amenity") or addr.get("leisure") or addr.get("road") or addr.get("neighbourhood") or addr.get("suburb")
        city = addr.get("city") or addr.get("town") or addr.get("village") or addr.get("county")
        parts = [p for p in [place, city] if p]
        result = ", ".join(parts) if parts else "Unknown location"
    except Exception:
        result = "Unknown location"
    cache[key] = result
    time.sleep(1.1)
    return result

def build_html(photos_by_day, reactions, voters, build_time_str, next_update_str):
    import html as htmlmod
    def esc(s): return htmlmod.escape(s or "")
    rail, mobile, days_out = [], [], []

    all_photos = [p for photos in photos_by_day.values() for p in photos]
    def top_photo(kind):
        candidates = [(p, len(voters.get(p["id"], {}).get(kind, []))) for p in all_photos]
        candidates = [c for c in candidates if c[1] > 0]
        if not candidates:
            return None, 0
        return max(candidates, key=lambda c: c[1])

    top_heart, heart_count = top_photo("heart")
    top_laugh, laugh_count = top_photo("laugh")
    top_down, down_count = top_photo("thumbsdown")

    def trophy_html(label, header_img, photo, count, kind):
        header = f'<img class="trophy-header" src="{header_img}" alt="{label}">'
        if not photo:
            return f'<div class="trophy-card">{header}<div class="trophy-empty">No votes yet</div></div>'
        names = voters.get(photo["id"], {}).get(kind, [])
        names_html = f'<div class="trophy-count">{esc(", ".join(names))}</div>' if names else ""
        thumb_src = photo.get('thumb_src') or photo.get('full_src')
        full_src = photo.get('full_src') or thumb_src
        video_attr = ' data-video="1"' if photo.get('type') == 'video' else ''
        return f'''<div class="trophy-card">{header}
<img class="photo-img" src="{IMG_BASE_URL}/{thumb_src}" data-full="{IMG_BASE_URL}/{full_src}"{video_attr} alt="">
<div class="trophy-count">{count} vote{'s' if count != 1 else ''}</div>{names_html}</div>'''

    IMG_BASE = "https://foreunder.github.io/conrad-family-album/book-images"
    IMG_BASE_URL = "https://foreunder.github.io/conrad-family-album"
    trophies_html = f'''<div class="trophies">{trophy_html("Most loved", IMG_BASE + "/category_most_loved.png", top_heart, heart_count, "heart")}{trophy_html("Funniest", IMG_BASE + "/category_funniest.png", top_laugh, laugh_count, "laugh")}{trophy_html("Yikes", IMG_BASE + "/category_yikes.png", top_down, down_count, "thumbsdown")}</div>'''

    for d in DAYS:
        key = d["key"]
        photos = photos_by_day.get(key, [])
        eyebrow = "Before the trip" if key == "PRE" else ("Unsorted" if key == "00" else f"Chapter {d['roman']}")
        cards = []
        for p in photos:
            needs = " needs-sort" if key == "00" else ""
            flag = '<div class="unsorted-flag">Needs sorting</div>' if key == "00" else ""
            pv = voters.get(p["id"], {})
            heart_names, laugh_names, down_names = pv.get("heart", []), pv.get("laugh", []), pv.get("thumbsdown", [])
            heart_n, laugh_n, down_n = len(heart_names), len(laugh_names), len(down_names)
            def names_attr(names): return esc(", ".join(names))
            reactor_bits = []
            if heart_names: reactor_bits.append(f"&#10084;&#65039; {esc(', '.join(heart_names))}")
            if laugh_names: reactor_bits.append(f"&#128514; {esc(', '.join(laugh_names))}")
            if down_names: reactor_bits.append(f"&#128078; {esc(', '.join(down_names))}")
            reactors_html = f'<div class="reactors">{" &middot; ".join(reactor_bits)}</div>' if reactor_bits else ""
            p_thumb_src = p.get('thumb_src') or p.get('full_src')
            p_full_src = p.get('full_src') or p_thumb_src
            is_video = p.get('type') == 'video'
            if is_video:
                media_html = f'<img src="{IMG_BASE_URL}/{p_thumb_src}" data-full="{IMG_BASE_URL}/{p_full_src}" data-video="1" alt=""><div class="play-badge">&#9658;</div>'
            else:
                media_html = f'<img src="{IMG_BASE_URL}/{p_thumb_src}" data-full="{IMG_BASE_URL}/{p_full_src}" alt="">'
            cards.append(f'''<div class="photo{needs}" data-photo-id="{esc(p['id'])}">{flag}{media_html}
<div class="reactions">
<button class="react-btn" data-reaction="heart" title="{names_attr(heart_names)}">&#10084;&#65039; <span class="rc">{heart_n}</span></button>
<button class="react-btn" data-reaction="laugh" title="{names_attr(laugh_names)}">&#128514; <span class="rc">{laugh_n}</span></button>
<button class="react-btn" data-reaction="thumbsdown" title="{names_attr(down_names)}">&#128078; <span class="rc">{down_n}</span></button>
</div>
<div class="cap"><div class="loc">{esc(p['loc'])}</div><div class="time">{esc(p['when'])}</div>{reactors_html}</div></div>''')
        empty = '<div class="day-empty">Nobody\'s added a photo here yet &mdash; get on that.</div>' if not photos else ""
        is_asu = key in ASU_DAYS
        accent = DAY_ACCENTS.get(key)
        day_class = "day asu" if is_asu else "day"
        day_attr = f' data-accent="1" style="--chapter-accent:{accent}"' if (accent and not is_asu) else ""
        title_html = f'{esc(d["title"])}<span class="asu-badge">Go Devils</span>' if is_asu else esc(d['title'])
        rail_class = "rail-item asu" if is_asu else "rail-item"
        rail_attr = f' data-accent="1" style="--chapter-accent:{accent}"' if (accent and not is_asu) else ""
        days_out.append(f'''<div id="day-{key}" class="{day_class}" data-key="{key}"{day_attr}>
<div class="chapter-date-row"><span class="chapter-eyebrow">{esc(eyebrow)}</span><span class="day-label">{esc(d['date'])}</span></div>
<h2 class="day-title">{title_html}</h2>
<p class="day-tag">{esc(TAGS.get(key,''))}</p>
<div class="photo-grid">{"".join(cards)}</div>{empty}</div>''')
        rail.append(f'''<a class="{rail_class}" data-key="{key}"{rail_attr} href="#day-{key}"><span class="num">{esc(d['roman'])}</span><div class="stack"><div class="lbl">{esc(d['railTitle'])}</div><div class="rdate">{esc(d['short'])}</div></div></a>''')
        mobile.append(f'<a data-key="{key}" href="#day-{key}">{esc(d["roman"])} &middot; {esc(d["short"])}</a>')

    style = open("style_block.html").read()

    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Blessed With This Time Together &middot; The Photo Album</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
{style}
</head><body>
<div id="whoModal">
<div class="box">
<p>Quick &mdash; who's tapping?</p>
<button class="who-btn" data-who="Jamey">Jamey</button>
<button class="who-btn" data-who="Kayla">Kayla</button>
<button class="who-btn" data-who="Jayden">Jayden</button>
<button class="who-btn" data-who="Kealey">Kealey</button>
<button class="who-btn" id="whoOtherToggle">Someone else</button>
<div class="who-other-row" id="whoOtherRow">
<input type="text" class="who-input" id="whoOtherInput" placeholder="Your name" maxlength="30">
<button class="who-go" id="whoOtherGo">Go</button>
</div>
</div>
</div>
<button id="manualRefreshBtn" title="Refresh now" aria-label="Refresh now">&#8635;</button>
<div class="hero"><div class="eyebrow">A Conrad family journey</div><h1>Blessed With This Time Together</h1>
<p>Eleven days chasing golf balls across Scotland, celebrating Kealey turning 25 in London, and watching ASU play Kansas at Wembley &mdash; because apparently that's a thing that happens now. Every photo below came from someone's actual camera roll.</p>
<div class="divider"></div><div class="dates">Sept 10 &ndash; 21, 2026</div>
<div style="margin-top:10px;"><a href="https://foreunder.github.io/conrad-family-album/book/" style="color:inherit;text-decoration:underline;font-size:13px;">Read the Book</a></div>
<div class="updated-stamp">Updated {build_time_str} &middot; next update around {next_update_str}</div></div>
{trophies_html}
<div class="mobile-nav">{"".join(mobile)}</div>
<div id="chapterProgress" class="chapter-progress"></div>
<div class="layout"><div class="rail">{"".join(rail)}</div><div class="thread"></div><div class="days">{"".join(days_out)}</div></div>
<div class="lightbox-overlay" id="lightbox"><button class="lightbox-close" id="lightboxClose" aria-label="Close">&times;</button>
<button class="lightbox-nav lightbox-prev" id="lightboxPrev" aria-label="Previous photo">&#8249;</button>
<button class="lightbox-nav lightbox-next" id="lightboxNext" aria-label="Next photo">&#8250;</button>
<div class="lightbox-content"><img id="lightboxImg" src="" alt=""><video id="lightboxVideo" controls playsinline style="display:none;max-width:100%;max-height:80vh;"></video><div class="lightbox-cap"><div class="loc" id="lightboxLoc"></div><div class="time" id="lightboxTime"></div><div class="reactions lightbox-reactions" id="lightboxReactions">
<button class="react-btn" data-reaction="heart">&#10084;&#65039; <span class="rc">0</span></button>
<button class="react-btn" data-reaction="laugh">&#128514; <span class="rc">0</span></button>
<button class="react-btn" data-reaction="thumbsdown">&#128078; <span class="rc">0</span></button>
</div></div></div></div>
<div class="footer-strip"><span>Made by the Conrads, one blister at a time</span><span>London &amp; Scotland &middot; September 2026</span></div>
<script>
// Each chapter is its own folder: exactly one .day section is shown at a
// time, picked from the rail/mobile nav, instead of one long page you have
// to scroll through to reach newer photos.
const TRIP_CHAPTER_KEYS = {json.dumps([d["key"] for d in DAYS if d["key"] not in ("PRE","00")])};
function updateProgress(key){{
  const el = document.getElementById('chapterProgress');
  if (!el) return;
  const idx = TRIP_CHAPTER_KEYS.indexOf(key);
  el.textContent = idx === -1 ? '' : ('Day ' + (idx + 1) + ' of ' + TRIP_CHAPTER_KEYS.length);
}}
function showDay(key, opts){{
  opts = opts || {{}};
  document.querySelectorAll('.day').forEach(el => el.classList.toggle('active', el.getAttribute('data-key') === key));
  document.querySelectorAll('.rail-item, .mobile-nav a').forEach(el => el.classList.toggle('active', el.getAttribute('data-key') === key));
  updateProgress(key);
  if (!opts.skipHash) history.replaceState(null, '', '#day-' + key);
  if (!opts.skipScroll) window.scrollTo({{ top: document.querySelector('.layout').offsetTop - 10, behavior: 'smooth' }});
  // Cosmetic nicety, kept last and defensive so it can never block the state
  // updates above (active chapter, progress text, URL hash, page scroll).
  try {{
    const activeChip = document.querySelector('.mobile-nav a.active');
    if (activeChip && activeChip.scrollIntoView) activeChip.scrollIntoView({{ behavior: 'smooth', inline: 'center', block: 'nearest' }});
  }} catch (err) {{ /* non-critical */ }}
}}
document.querySelectorAll('.rail-item, .mobile-nav a').forEach(el => {{
  el.addEventListener('click', (e) => {{
    e.preventDefault();
    showDay(el.getAttribute('data-key'));
  }});
}});
// Land on whichever chapter the URL points to, else the most recent
// chapter that actually has photos in it -- new photos should never be
// more than one tap away.
(function initialDay(){{
  const hashKey = (location.hash || '').replace('#day-', '');
  const dayEls = Array.from(document.querySelectorAll('.day'));
  let target = dayEls.find(el => el.getAttribute('data-key') === hashKey);
  if (!target){{
    const withPhotos = dayEls.filter(el => el.querySelector('.photo'));
    target = withPhotos.length ? withPhotos[withPhotos.length - 1] : dayEls[dayEls.length - 1];
  }}
  if (target) showDay(target.getAttribute('data-key'), {{ skipScroll: true }});
}})();
// Swipe left/right between chapters on touch devices, so flipping through
// days feels like turning pages instead of always reaching for the nav bar.
// Ignored while the lightbox is open, or when the gesture is more vertical
// (a normal scroll) than horizontal, so it never fights page scrolling or
// photo viewing.
(function enableSwipeNav(){{
  const daysContainer = document.querySelector('.days');
  if (!daysContainer) return;
  let startX = 0, startY = 0, tracking = false;
  daysContainer.addEventListener('touchstart', (e) => {{
    if (lightbox.classList.contains('open') || e.touches.length !== 1) {{ tracking = false; return; }}
    startX = e.touches[0].clientX;
    startY = e.touches[0].clientY;
    tracking = true;
  }}, {{ passive: true }});
  daysContainer.addEventListener('touchend', (e) => {{
    if (!tracking) return;
    tracking = false;
    const dx = e.changedTouches[0].clientX - startX;
    const dy = e.changedTouches[0].clientY - startY;
    if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
    const dayEls = Array.from(document.querySelectorAll('.day'));
    const activeIdx = dayEls.findIndex(el => el.classList.contains('active'));
    if (activeIdx === -1) return;
    const nextIdx = dx < 0 ? activeIdx + 1 : activeIdx - 1;
    if (nextIdx < 0 || nextIdx >= dayEls.length) return;
    showDay(dayEls[nextIdx].getAttribute('data-key'));
  }}, {{ passive: true }});
}})();
const lightbox = document.getElementById('lightbox'), lbImg = document.getElementById('lightboxImg'), lbVideo = document.getElementById('lightboxVideo'), lbLoc = document.getElementById('lightboxLoc'), lbTime = document.getElementById('lightboxTime');
function showLightboxMedia(img){{
  if (img.dataset.video === '1'){{
    lbImg.style.display = 'none';
    lbVideo.style.display = 'block';
    lbVideo.src = img.dataset.full || img.src;
    lbVideo.load();
  }} else {{
    lbVideo.pause(); lbVideo.removeAttribute('src'); lbVideo.style.display = 'none';
    lbImg.style.display = 'block';
    lbImg.src = img.dataset.full || img.src;
  }}
}}
let currentCard = null;
function syncLightboxReactions(card){{
  const lbButtons = document.querySelectorAll('#lightboxReactions .react-btn');
  const cardButtons = card.querySelectorAll('.reactions:not(.lightbox-reactions) .react-btn');
  lbButtons.forEach(lbBtn => {{
    const reaction = lbBtn.getAttribute('data-reaction');
    const cardBtn = Array.from(cardButtons).find(b => b.getAttribute('data-reaction') === reaction);
    if (!cardBtn) return;
    lbBtn.querySelector('.rc').textContent = cardBtn.querySelector('.rc').textContent;
    lbBtn.classList.toggle('voted', cardBtn.classList.contains('voted'));
  }});
}}
function openLightbox(card){{ currentCard = card; const img = card.querySelector('img'), loc = card.querySelector('.cap .loc'), tm = card.querySelector('.cap .time');
  showLightboxMedia(img); lbLoc.textContent = loc ? loc.textContent : ''; lbTime.textContent = tm ? tm.textContent : ''; syncLightboxReactions(card); lightbox.classList.add('open'); document.body.style.overflow = 'hidden'; }}
function closeLightbox(){{ lightbox.classList.remove('open'); currentCard = null; document.body.style.overflow = ''; lbImg.style.transform=''; lbImg.style.opacity=''; lbVideo.pause(); }}
function navigateLightbox(dir){{ if (!currentCard) return; const sib = Array.from(currentCard.parentElement.querySelectorAll('.photo')); const idx = sib.indexOf(currentCard); if (idx===-1) return; openLightbox(sib[(idx+dir+sib.length)%sib.length]); }}
document.querySelectorAll('.photo img').forEach(img => img.addEventListener('click', () => openLightbox(img.closest('.photo'))));
document.querySelectorAll('.trophy-card .photo-img').forEach(img => {{ img.style.cursor = 'pointer'; img.addEventListener('click', () => {{ showLightboxMedia(img); lbLoc.textContent = ''; lbTime.textContent = ''; lightbox.classList.add('open'); document.body.style.overflow = 'hidden'; }}); }});
document.getElementById('lightboxClose').addEventListener('click', closeLightbox);
document.getElementById('lightboxImg').addEventListener('click', closeLightbox);
document.getElementById('lightboxPrev').addEventListener('click', () => navigateLightbox(-1));
document.getElementById('lightboxNext').addEventListener('click', () => navigateLightbox(1));
lightbox.addEventListener('click', e => {{ if (e.target === lightbox) closeLightbox(); }});
document.addEventListener('keydown', e => {{ if (!lightbox.classList.contains('open')) return; if (e.key==='Escape') closeLightbox(); else if (e.key==='ArrowLeft') navigateLightbox(-1); else if (e.key==='ArrowRight') navigateLightbox(1); }});
let touchStartY = 0, touchDeltaY = 0, touching = false;
lightbox.addEventListener('touchstart', e => {{ touching = true; touchStartY = e.touches[0].clientY; }}, {{passive:true}});
lightbox.addEventListener('touchmove', e => {{
  if (!touching) return;
  touchDeltaY = e.touches[0].clientY - touchStartY;
  if (touchDeltaY > 0) {{
    e.preventDefault();
    lbImg.style.transform = 'translateY(' + touchDeltaY + 'px)';
    lbImg.style.opacity = Math.max(1 - touchDeltaY / 300, 0.2);
  }}
}}, {{passive:false}});
lightbox.addEventListener('touchend', () => {{
  if (touching && touchDeltaY > 80) {{ closeLightbox(); }}
  else {{ lbImg.style.transform=''; lbImg.style.opacity=''; }}
  touching = false; touchDeltaY = 0;
}});

const REACTIONS_ENDPOINT = "{REACTIONS_ENDPOINT}";

function getVoterName() {{
  return localStorage.getItem('voterName');
}}

function sendReaction(photoId, reaction) {{
  const voter = getVoterName();
  fetch(REACTIONS_ENDPOINT, {{
    method: 'POST',
    body: JSON.stringify({{ photoId, reaction, voter }})
  }}).catch(() => {{}});
}}

let pendingReaction = null;
const whoModal = document.getElementById('whoModal');

function confirmVoter(name) {{
  localStorage.setItem('voterName', name);
  whoModal.classList.remove('show');
  if (pendingReaction) {{
    sendReaction(pendingReaction.photoId, pendingReaction.reaction);
    pendingReaction = null;
  }}
}}

document.querySelectorAll('.who-btn[data-who]').forEach(btn => {{
  btn.addEventListener('click', () => confirmVoter(btn.getAttribute('data-who')));
}});

document.getElementById('whoOtherToggle').addEventListener('click', () => {{
  const row = document.getElementById('whoOtherRow');
  row.classList.add('show');
  document.getElementById('whoOtherInput').focus();
}});

function submitOtherName() {{
  const input = document.getElementById('whoOtherInput');
  const name = input.value.trim();
  if (!name) {{ input.focus(); return; }}
  confirmVoter(name);
}}
document.getElementById('whoOtherGo').addEventListener('click', submitOtherName);
document.getElementById('whoOtherInput').addEventListener('keydown', (e) => {{
  if (e.key === 'Enter') submitOtherName();
}});

document.querySelectorAll('.react-btn').forEach(btn => {{
  btn.addEventListener('click', (e) => {{
    e.stopPropagation();
    if (!REACTIONS_ENDPOINT) return;
    const fromLightbox = !!btn.closest('#lightboxReactions');
    const card = fromLightbox ? currentCard : btn.closest('.photo');
    if (!card) return;
    const photoId = card.getAttribute('data-photo-id');
    const reaction = btn.getAttribute('data-reaction');
    const countEl = btn.querySelector('.rc');
    countEl.textContent = parseInt(countEl.textContent, 10) + 1;
    btn.classList.add('voted');
    // Keep the card and lightbox reaction counts in sync with each other,
    // since a click on either one only updates itself by default.
    const mirrorSelector = fromLightbox ? '.reactions:not(.lightbox-reactions) .react-btn' : '#lightboxReactions .react-btn';
    const mirrorRoot = fromLightbox ? card : document;
    const mirrorBtn = Array.from(mirrorRoot.querySelectorAll(mirrorSelector)).find(b => b.getAttribute('data-reaction') === reaction);
    if (mirrorBtn) {{
      mirrorBtn.querySelector('.rc').textContent = countEl.textContent;
      mirrorBtn.classList.add('voted');
    }}
    if (!getVoterName()) {{
      pendingReaction = {{ photoId, reaction }};
      whoModal.classList.add('show');
      return;
    }}
    sendReaction(photoId, reaction);
  }});
}});

// Auto-reload every 15 minutes so new photos/votes appear without a manual refresh.
// Preserves scroll position, and skips the reload if the name-prompt modal is open.
(function() {{
  const savedScroll = sessionStorage.getItem('albumScrollY');
  if (savedScroll !== null) {{
    window.scrollTo(0, parseInt(savedScroll, 10));
    sessionStorage.removeItem('albumScrollY');
  }}
  setInterval(function() {{
    if (whoModal.classList.contains('show')) return; // don't interrupt someone mid-vote
    sessionStorage.setItem('albumScrollY', window.scrollY);
    location.reload();
  }}, 15 * 60 * 1000);
}})();

// Manual refresh button — mainly for Home Screen / standalone mode on iOS,
// where pull-to-refresh doesn't work since there's no Safari chrome.
const manualRefreshBtn = document.getElementById('manualRefreshBtn');
if (manualRefreshBtn) {{
  manualRefreshBtn.addEventListener('click', function() {{
    manualRefreshBtn.classList.add('spinning');
    sessionStorage.setItem('albumScrollY', window.scrollY);
    location.reload();
  }});
}}
</script></body></html>'''

def main():
    service = get_drive_service()
    files = list_photos(service)
    video_files = list_videos(service)
    cache = json.load(open(CACHE_PATH)) if os.path.exists(CACHE_PATH) else {}
    photos_by_day = {d["key"]: [] for d in DAYS}
    seen_hashes = {}  # sha256 -> first file id that had it, for exact-duplicate skipping
    kept_bursts = []  # (datetime, dhash) for photos already kept this run, for near-duplicate burst skipping
    BURST_WINDOW_SECONDS = 120
    BURST_HAMMING_THRESHOLD = 10  # out of 64 bits; lower = stricter match

    for f in files:
        raw = download_file(service, f["id"])
        content_hash = hashlib.sha256(raw).hexdigest()
        if content_hash in seen_hashes:
            print(f"Skipping {f['name']} ({f['id']}) — exact duplicate of {seen_hashes[content_hash]}")
            continue
        seen_hashes[content_hash] = f["name"]
        date_obj, lat, lon, date_is_upload_only = extract_exif(raw, f["name"], drive_file=f)
        full_src, thumb_src, phash = to_web_image(raw, f["name"], f["id"])
        if full_src is None:
            continue
        if date_obj is not None and phash is not None:
            is_burst_duplicate = False
            for kept_date, kept_hash in kept_bursts:
                if abs((date_obj - kept_date).total_seconds()) <= BURST_WINDOW_SECONDS \
                        and hamming_distance(phash, kept_hash) <= BURST_HAMMING_THRESHOLD:
                    is_burst_duplicate = True
                    break
            if is_burst_duplicate:
                print(f"Skipping {f['name']} ({f['id']}) — near-duplicate burst shot")
                continue
            kept_bursts.append((date_obj, phash))
        day_key = "00"
        if date_obj and not date_is_upload_only:
            iso = date_obj.strftime("%Y-%m-%d")
            if iso in date_to_day:
                day_key = date_to_day[iso]
            elif PRE_TRIP_WINDOW_START <= iso < TRIP_START:
                day_key = "PRE"
        loc = "No location data"
        if lat is not None:
            loc = reverse_geocode(lat, lon, cache)
        elif day_key != "00":
            loc = "Location unknown"
        if date_obj is None:
            when = "Date unknown"
        elif date_is_upload_only:
            when = date_obj.strftime("Added %a, %b %-d")
        else:
            when = date_obj.strftime("%a, %b %-d, %-I:%M %p")
        photos_by_day[day_key].append({"id": f["id"], "type": "photo", "full_src": full_src, "thumb_src": thumb_src, "loc": loc, "when": when, "date": date_obj.isoformat() if date_obj else ""})

    for f in video_files:
        raw = download_file(service, f["id"])
        content_hash = hashlib.sha256(raw).hexdigest()
        if content_hash in seen_hashes:
            print(f"Skipping {f['name']} ({f['id']}) — exact duplicate of {seen_hashes[content_hash]}")
            continue
        seen_hashes[content_hash] = f["name"]

        video_src, poster_src, video_path = to_web_video(raw, f["name"], f["id"])
        if video_src is None:
            continue
        date_obj, date_is_upload_only = extract_video_date(video_path, f)

        day_key = "00"
        if date_obj and not date_is_upload_only:
            iso = date_obj.strftime("%Y-%m-%d")
            if iso in date_to_day:
                day_key = date_to_day[iso]
            elif PRE_TRIP_WINDOW_START <= iso < TRIP_START:
                day_key = "PRE"
        loc = "Location unknown" if day_key != "00" else "No location data"
        if date_obj is None:
            when = "Date unknown"
        elif date_is_upload_only:
            when = date_obj.strftime("Added %a, %b %-d")
        else:
            when = date_obj.strftime("%a, %b %-d, %-I:%M %p")

        photos_by_day[day_key].append({"id": f["id"], "type": "video", "full_src": video_src, "thumb_src": poster_src, "loc": loc, "when": when, "date": date_obj.isoformat() if date_obj else ""})

    for k in photos_by_day:
        photos_by_day[k].sort(key=lambda p: p["date"] or "9999")

    # Stage the photo files ourselves. The workflow's own `git add` only lists
    # index.html and the two cache files (it predates this folder existing),
    # so without this step every image written above gets thrown away the
    # moment this run ends — the page would reference photos that were never
    # actually saved to the repo. Staging here means they get swept into
    # whatever commit the workflow makes next, regardless of that list.
    if os.path.isdir(ALBUM_IMAGE_DIR):
        subprocess.run(["git", "add", ALBUM_IMAGE_DIR], check=False)

    json.dump(cache, open(CACHE_PATH, "w"))
    reactions = fetch_reactions()
    voters = fetch_voters()
    now = datetime.now(_TZ)
    build_time_str = now.strftime("%b %-d, %Y \u00b7 %-I:%M\u00a0%p\u00a0CT")
    minutes_to_next = 15 - (now.minute % 15)
    next_update = now + timedelta(minutes=minutes_to_next)
    next_update_str = next_update.strftime("%-I:%M\u00a0%p\u00a0CT")
    html_out = build_html(photos_by_day, reactions, voters, build_time_str, next_update_str)
    with open("index.html", "w") as f:
        f.write(html_out)
    total = sum(len(v) for v in photos_by_day.values())
    print(f"Built index.html with {total} photos")

if __name__ == "__main__":
    main()

