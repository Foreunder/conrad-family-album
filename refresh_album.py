import os, io, json, base64, subprocess, sys
from datetime import datetime, timezone, timedelta

from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload
import exifread
import pillow_heif
from PIL import Image
import urllib.request, urllib.parse, time

DRIVE_FOLDER_ID = "1idR48mqlXyY1Jjh8E0ZxpnG2N5fqUhUN"
SCOPES = ["https://www.googleapis.com/auth/drive.readonly"]
KEY_PATH = os.environ.get("SERVICE_ACCOUNT_KEY_PATH", "sa_key.json")
CACHE_PATH = "geocode_cache.json"

TRIP_START = "2026-09-10"
PRE_TRIP_WINDOW_START = "2026-08-11"

DAYS = [
  {"key":"PRE","roman":"\u2014","title":"Before We Go: Packing & Planning","date":"Aug 11\u2013Sept 9, 2026","dates":[]},
  {"key":"01","roman":"I","title":"The Distance Between Here and There","date":"Thu\u2013Fri, Sept 10\u201311, 2026","dates":["2026-09-10","2026-09-11"]},
  {"key":"02","roman":"II","title":"Kealey's Birthday in London","date":"Saturday, Sept 12, 2026","dates":["2026-09-12"]},
  {"key":"03","roman":"III","title":"Westminster and the Thames","date":"Sunday, Sept 13, 2026","dates":["2026-09-13"]},
  {"key":"04","roman":"IV","title":"North to Scotland","date":"Monday, Sept 14, 2026","dates":["2026-09-14"]},
  {"key":"05","roman":"V","title":"Gullane and the Road to St Andrews","date":"Tuesday, Sept 15, 2026","dates":["2026-09-15"]},
  {"key":"06","roman":"VI","title":"St Andrews and Edinburgh","date":"Wednesday, Sept 16, 2026","dates":["2026-09-16"]},
  {"key":"07","roman":"VII","title":"London's South Bank","date":"Thursday, Sept 17, 2026","dates":["2026-09-17"]},
  {"key":"08","roman":"VIII","title":"On to Wembley","date":"Friday, Sept 18, 2026","dates":["2026-09-18"]},
  {"key":"09","roman":"IX","title":"Game Day at Wembley","date":"Saturday, Sept 19, 2026","dates":["2026-09-19"]},
  {"key":"10","roman":"X","title":"London to Phoenix","date":"Sunday, Sept 20, 2026","dates":["2026-09-20"]},
  {"key":"11","roman":"XI","title":"The Road Home","date":"Monday, Sept 21, 2026","dates":["2026-09-21"]},
  {"key":"00","roman":"?","title":"Unsorted","date":"No date or GPS data found","dates":[]},
]
TAGS = {
  "PRE":"Compression cubes, a Garmin charger, and mounting anticipation.",
  "01":"Two cities, one flight, and by morning we're all in the same country again.",
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

def download_file(service, file_id):
    request = service.files().get_media(fileId=file_id)
    buf = io.BytesIO()
    downloader = MediaIoBaseDownload(buf, request)
    done = False
    while not done:
        _, done = downloader.next_chunk()
    buf.seek(0)
    return buf.read()

def extract_exif(raw_bytes, filename):
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
    return date_obj, lat, lon

def to_web_image(raw_bytes, filename):
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = img.convert("RGB")
        img.thumbnail((1600, 1600))
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=82)
        return base64.b64encode(out.getvalue()).decode("ascii")
    except Exception:
        return None

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

def build_html(photos_by_day):
    import html as htmlmod
    def esc(s): return htmlmod.escape(s or "")
    rail, mobile, days_out = [], [], []
    for d in DAYS:
        key = d["key"]
        photos = photos_by_day.get(key, [])
        eyebrow = "Before the trip" if key == "PRE" else ("Unsorted" if key == "00" else f"Chapter {d['roman']}")
        cards = []
        for p in photos:
            needs = " needs-sort" if key == "00" else ""
            flag = '<div class="unsorted-flag">Needs sorting</div>' if key == "00" else ""
            cards.append(f'''<div class="photo{needs}">{flag}<img src="data:image/jpeg;base64,{p['b64']}" alt="">
<div class="cap"><div class="loc">{esc(p['loc'])}</div><div class="time">{esc(p['when'])}</div></div></div>''')
        empty = '<div class="day-empty">Nobody\'s added a photo here yet &mdash; get on that.</div>' if not photos else ""
        days_out.append(f'''<div id="day-{key}" class="day" data-key="{key}">
<div class="chapter-date-row"><span class="chapter-eyebrow">{esc(eyebrow)}</span><span class="day-label">{esc(d['date'])}</span></div>
<h2 class="day-title">{esc(d['title'])}</h2>
<p class="day-tag">{esc(TAGS.get(key,''))}</p>
<div class="photo-grid">{"".join(cards)}</div>{empty}</div>''')
        rail.append(f'''<a class="rail-item" data-key="{key}" href="#day-{key}"><span class="num">{esc(d['roman'])}</span><div class="stack"><div class="lbl">{esc(d['title'])}</div><div class="rdate">{len(photos)} photo{'s' if len(photos)!=1 else ''}</div></div></a>''')
        mobile.append(f'<a data-key="{key}" href="#day-{key}">{esc(d["roman"])}</a>')

    style = open("style_block.html").read()

    return f'''<!DOCTYPE html>
<html lang="en"><head><meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Blessed With This Time Together &middot; The Photo Album</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,400;9..144,500;9..144,600&family=Inter:wght@400;500;600&display=swap" rel="stylesheet">
{style}
</head><body>
<div class="hero"><div class="eyebrow">A Conrad family journey</div><h1>Blessed With This Time Together</h1>
<p>Eleven days chasing golf balls across Scotland, celebrating Kealey turning 25 in London, and watching ASU play Kansas at Wembley &mdash; because apparently that's a thing that happens now. Every photo below came from someone's actual camera roll.</p>
<div class="divider"></div><div class="dates">Sept 10 &ndash; 21, 2026</div></div>
<div class="mobile-nav">{"".join(mobile)}</div>
<div class="layout"><div class="rail">{"".join(rail)}</div><div class="thread"></div><div class="days">{"".join(days_out)}</div></div>
<div class="lightbox-overlay" id="lightbox"><button class="lightbox-close" id="lightboxClose" aria-label="Close">&times;</button>
<button class="lightbox-nav lightbox-prev" id="lightboxPrev" aria-label="Previous photo">&#8249;</button>
<button class="lightbox-nav lightbox-next" id="lightboxNext" aria-label="Next photo">&#8250;</button>
<div class="lightbox-content"><img id="lightboxImg" src="" alt=""><div class="lightbox-cap"><div class="loc" id="lightboxLoc"></div><div class="time" id="lightboxTime"></div></div></div></div>
<div class="footer-strip"><span>Made by the Conrads, one blister at a time</span><span>London &amp; Scotland &middot; September 2026</span></div>
<script>
const observer = new IntersectionObserver((entries) => {{
  entries.forEach(entry => {{
    if (!entry.isIntersecting) return;
    const key = entry.target.getAttribute('data-key');
    document.querySelectorAll('.rail-item, .mobile-nav a').forEach(el => el.classList.toggle('active', el.getAttribute('data-key') === key));
  }});
}}, {{ rootMargin: '-40% 0px -55% 0px' }});
document.querySelectorAll('.day').forEach(el => observer.observe(el));
const lightbox = document.getElementById('lightbox'), lbImg = document.getElementById('lightboxImg'), lbLoc = document.getElementById('lightboxLoc'), lbTime = document.getElementById('lightboxTime');
let currentCard = null;
function openLightbox(card){{ currentCard = card; const img = card.querySelector('img'), loc = card.querySelector('.cap .loc'), tm = card.querySelector('.cap .time');
  lbImg.src = img.src; lbLoc.textContent = loc ? loc.textContent : ''; lbTime.textContent = tm ? tm.textContent : ''; lightbox.classList.add('open'); }}
function closeLightbox(){{ lightbox.classList.remove('open'); currentCard = null; }}
function navigateLightbox(dir){{ if (!currentCard) return; const sib = Array.from(currentCard.parentElement.querySelectorAll('.photo')); const idx = sib.indexOf(currentCard); if (idx===-1) return; openLightbox(sib[(idx+dir+sib.length)%sib.length]); }}
document.querySelectorAll('.photo img').forEach(img => img.addEventListener('click', () => openLightbox(img.closest('.photo'))));
document.getElementById('lightboxClose').addEventListener('click', closeLightbox);
document.getElementById('lightboxPrev').addEventListener('click', () => navigateLightbox(-1));
document.getElementById('lightboxNext').addEventListener('click', () => navigateLightbox(1));
lightbox.addEventListener('click', e => {{ if (e.target === lightbox) closeLightbox(); }});
document.addEventListener('keydown', e => {{ if (!lightbox.classList.contains('open')) return; if (e.key==='Escape') closeLightbox(); else if (e.key==='ArrowLeft') navigateLightbox(-1); else if (e.key==='ArrowRight') navigateLightbox(1); }});
</script></body></html>'''

def main():
    service = get_drive_service()
    files = list_photos(service)
    cache = json.load(open(CACHE_PATH)) if os.path.exists(CACHE_PATH) else {}
    photos_by_day = {d["key"]: [] for d in DAYS}

    for f in files:
        raw = download_file(service, f["id"])
        date_obj, lat, lon = extract_exif(raw, f["name"])
        b64 = to_web_image(raw, f["name"])
        if b64 is None:
            continue
        day_key = "00"
        if date_obj:
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
        when = date_obj.strftime("%a, %-I:%M %p") if date_obj else "Date unknown"
        photos_by_day[day_key].append({"b64": b64, "loc": loc, "when": when, "date": date_obj.isoformat() if date_obj else ""})

    for k in photos_by_day:
        photos_by_day[k].sort(key=lambda p: p["date"] or "9999")

    json.dump(cache, open(CACHE_PATH, "w"))
    html_out = build_html(photos_by_day)
    with open("index.html", "w") as f:
        f.write(html_out)
    total = sum(len(v) for v in photos_by_day.values())
    print(f"Built index.html with {total} photos")

if __name__ == "__main__":
    main()
