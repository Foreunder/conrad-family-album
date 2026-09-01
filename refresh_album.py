import os, io, json, base64, subprocess, sys
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
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read())
    except Exception:
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

def to_web_image(raw_bytes, filename):
    try:
        img = Image.open(io.BytesIO(raw_bytes))
        img = ImageOps.exif_transpose(img)
        img = img.convert("RGB")

        full = img.copy()
        full.thumbnail((1600, 1600))
        out_full = io.BytesIO()
        full.save(out_full, format="JPEG", quality=82)

        thumb = img.copy()
        thumb.thumbnail((600, 600))
        out_thumb = io.BytesIO()
        thumb.save(out_thumb, format="JPEG", quality=82)

        return (
            base64.b64encode(out_full.getvalue()).decode("ascii"),
            base64.b64encode(out_thumb.getvalue()).decode("ascii"),
        )
    except Exception:
        return None, None

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

def build_html(photos_by_day, reactions, build_time_str):
    import html as htmlmod
    def esc(s): return htmlmod.escape(s or "")
    rail, mobile, days_out = [], [], []

    all_photos = [p for photos in photos_by_day.values() for p in photos]
    def top_photo(kind):
        candidates = [(p, reactions.get(p["id"], {}).get(kind, 0)) for p in all_photos]
        candidates = [c for c in candidates if c[1] > 0]
        if not candidates:
            return None, 0
        return max(candidates, key=lambda c: c[1])

    top_heart, heart_count = top_photo("heart")
    top_laugh, laugh_count = top_photo("laugh")
    top_down, down_count = top_photo("thumbsdown")

    def trophy_html(label, header_img, photo, count):
        header = f'<img class="trophy-header" src="{header_img}" alt="{label}">'
        if not photo:
            return f'<div class="trophy-card">{header}<div class="trophy-empty">No votes yet</div></div>'
        return f'''<div class="trophy-card">{header}
<img class="photo-img" src="data:image/jpeg;base64,{photo.get('b64_thumb') or photo['b64']}" data-full="data:image/jpeg;base64,{photo['b64']}" alt="">
<div class="trophy-count">{count} vote{'s' if count != 1 else ''}</div></div>'''

    IMG_BASE = "https://foreunder.github.io/conrad-family-album/book-images"
    trophies_html = f'''<div class="trophies">{trophy_html("Most loved", IMG_BASE + "/category_most_loved.png", top_heart, heart_count)}{trophy_html("Funniest", IMG_BASE + "/category_funniest.png", top_laugh, laugh_count)}{trophy_html("Yikes", IMG_BASE + "/category_yikes.png", top_down, down_count)}</div>'''

    for d in DAYS:
        key = d["key"]
        photos = photos_by_day.get(key, [])
        eyebrow = "Before the trip" if key == "PRE" else ("Unsorted" if key == "00" else f"Chapter {d['roman']}")
        cards = []
        for p in photos:
            needs = " needs-sort" if key == "00" else ""
            flag = '<div class="unsorted-flag">Needs sorting</div>' if key == "00" else ""
            r = reactions.get(p["id"], {})
            heart_n, laugh_n, down_n = r.get("heart", 0), r.get("laugh", 0), r.get("thumbsdown", 0)
            cards.append(f'''<div class="photo{needs}" data-photo-id="{esc(p['id'])}">{flag}<img src="data:image/jpeg;base64,{p.get('b64_thumb') or p['b64']}" data-full="data:image/jpeg;base64,{p['b64']}" alt="">
<div class="reactions">
<button class="react-btn" data-reaction="heart">&#10084;&#65039; <span class="rc">{heart_n}</span></button>
<button class="react-btn" data-reaction="laugh">&#128514; <span class="rc">{laugh_n}</span></button>
<button class="react-btn" data-reaction="thumbsdown">&#128078; <span class="rc">{down_n}</span></button>
</div>
<div class="cap"><div class="loc">{esc(p['loc'])}</div><div class="time">{esc(p['when'])}</div></div></div>''')
        empty = '<div class="day-empty">Nobody\'s added a photo here yet &mdash; get on that.</div>' if not photos else ""
        days_out.append(f'''<div id="day-{key}" class="day" data-key="{key}">
<div class="chapter-date-row"><span class="chapter-eyebrow">{esc(eyebrow)}</span><span class="day-label">{esc(d['date'])}</span></div>
<h2 class="day-title">{esc(d['title'])}</h2>
<p class="day-tag">{esc(TAGS.get(key,''))}</p>
<div class="photo-grid">{"".join(cards)}</div>{empty}</div>''')
        rail.append(f'''<a class="rail-item" data-key="{key}" href="#day-{key}"><span class="num">{esc(d['roman'])}</span><div class="stack"><div class="lbl">{esc(d['railTitle'])}</div><div class="rdate">{esc(d['short'])}</div></div></a>''')
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
<div class="divider"></div><div class="dates">Sept 10 &ndash; 21, 2026</div>
<div class="updated-stamp">Updated {build_time_str}</div></div>
{trophies_html}
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
    const activeChip = document.querySelector('.mobile-nav a.active');
    if (activeChip) activeChip.scrollIntoView({{ behavior: 'smooth', inline: 'center', block: 'nearest' }});
  }});
}}, {{ rootMargin: '-40% 0px -55% 0px' }});
document.querySelectorAll('.day').forEach(el => observer.observe(el));
const lightbox = document.getElementById('lightbox'), lbImg = document.getElementById('lightboxImg'), lbLoc = document.getElementById('lightboxLoc'), lbTime = document.getElementById('lightboxTime');
let currentCard = null;
function openLightbox(card){{ currentCard = card; const img = card.querySelector('img'), loc = card.querySelector('.cap .loc'), tm = card.querySelector('.cap .time');
  lbImg.src = img.dataset.full || img.src; lbLoc.textContent = loc ? loc.textContent : ''; lbTime.textContent = tm ? tm.textContent : ''; lightbox.classList.add('open'); document.body.style.overflow = 'hidden'; }}
function closeLightbox(){{ lightbox.classList.remove('open'); currentCard = null; document.body.style.overflow = ''; lbImg.style.transform=''; lbImg.style.opacity=''; }}
function navigateLightbox(dir){{ if (!currentCard) return; const sib = Array.from(currentCard.parentElement.querySelectorAll('.photo')); const idx = sib.indexOf(currentCard); if (idx===-1) return; openLightbox(sib[(idx+dir+sib.length)%sib.length]); }}
document.querySelectorAll('.photo img').forEach(img => img.addEventListener('click', () => openLightbox(img.closest('.photo'))));
document.querySelectorAll('.trophy-card .photo-img').forEach(img => {{ img.style.cursor = 'pointer'; img.addEventListener('click', () => {{ lbImg.src = img.dataset.full || img.src; lbLoc.textContent = ''; lbTime.textContent = ''; lightbox.classList.add('open'); document.body.style.overflow = 'hidden'; }}); }});
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
document.querySelectorAll('.react-btn').forEach(btn => {{
  btn.addEventListener('click', (e) => {{
    e.stopPropagation();
    if (!REACTIONS_ENDPOINT) return;
    const card = btn.closest('.photo');
    const photoId = card.getAttribute('data-photo-id');
    const reaction = btn.getAttribute('data-reaction');
    const countEl = btn.querySelector('.rc');
    countEl.textContent = parseInt(countEl.textContent, 10) + 1;
    btn.classList.add('voted');
    fetch(REACTIONS_ENDPOINT, {{
      method: 'POST',
      body: JSON.stringify({{ photoId, reaction }})
    }}).catch(() => {{}});
  }});
}});
</script></body></html>'''

def main():
    service = get_drive_service()
    files = list_photos(service)
    cache = json.load(open(CACHE_PATH)) if os.path.exists(CACHE_PATH) else {}
    photos_by_day = {d["key"]: [] for d in DAYS}

    for f in files:
        raw = download_file(service, f["id"])
        date_obj, lat, lon, date_is_upload_only = extract_exif(raw, f["name"], drive_file=f)
        b64, b64_thumb = to_web_image(raw, f["name"])
        if b64 is None:
            continue
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
            when = date_obj.strftime("%a, %-I:%M %p")
        photos_by_day[day_key].append({"id": f["id"], "b64": b64, "b64_thumb": b64_thumb, "loc": loc, "when": when, "date": date_obj.isoformat() if date_obj else ""})

    for k in photos_by_day:
        photos_by_day[k].sort(key=lambda p: p["date"] or "9999")

    json.dump(cache, open(CACHE_PATH, "w"))
    reactions = fetch_reactions()
    now = datetime.now(_TZ)
    build_time_str = now.strftime("%b %-d, %Y \u00b7 %-I:%M %p") + " CT"
    html_out = build_html(photos_by_day, reactions, build_time_str)
    with open("index.html", "w") as f:
        f.write(html_out)
    total = sum(len(v) for v in photos_by_day.values())
    print(f"Built index.html with {total} photos")

if __name__ == "__main__":
    main()
