#!/usr/bin/env python3
"""Ryan Air morning flight briefing.

Collects weather, hazards, NOTAMs and runway information for the Ryan Air hubs
and their surrounding villages, scores flyability, and writes a self-contained
HTML page to out/briefing.html (with a dated copy in out/archive/).

Sources: aviationweather.gov (METAR, TAF, PIREP, SIGMET, airport data),
api.weather.gov (AAWU area forecasts/AIRMETs/SIGMETs, forecast discussions,
alerts, point forecasts), NEXRAD RIDGE radar imagery, Open-Meteo (model
forecast for villages with no weather station), optional Weather Underground
PWS (WU_API_KEY) and optional FAA NOTAM API (FAA_CLIENT_ID/FAA_CLIENT_SECRET).

Stdlib only. Usage: python3 briefing.py [--no-ai] [--open]
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import html
import json
import math
import os
import re
import shutil
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
UA = "RyanAirBriefing/1.0 (ops briefing tool)"
AK_TZ = dt.timezone(dt.timedelta(hours=-8), "AKDT")  # replaced at runtime if zoneinfo available
try:
    from zoneinfo import ZoneInfo
    AK_TZ = ZoneInfo("America/Anchorage")
except Exception:
    pass

HUBS = {
    "PANC": {"name": "Anchorage", "radius": 45, "fa": ["COOK INLET"]},
    "PANI": {"name": "Aniak", "radius": 90, "fa": ["KUSKOKWIM"]},
    "PABE": {"name": "Bethel", "radius": 100, "fa": ["KUSKOKWIM DELTA", "Y-K DELTA", "YK DELTA", "KUSKOKWIM"]},
    "PASM": {"name": "St. Mary's", "radius": 60, "fa": ["LWR YUKON", "Y-K DELTA", "YK DELTA", "KUSKOKWIM DELTA"]},
    "PAEM": {"name": "Emmonak", "radius": 60, "fa": ["LWR YUKON", "Y-K DELTA", "YK DELTA", "KUSKOKWIM DELTA"]},
    "PAUN": {"name": "Unalakleet", "radius": 90, "fa": ["NORTON SOUND"]},
    "PAOM": {"name": "Nome", "radius": 150, "fa": ["SEWARD PEN", "ST LAWRENCE"]},
    "PAOT": {"name": "Kotzebue", "radius": 150, "fa": ["KOBUK", "NOATAK", "KOTZEBUE"]},
    "PADQ": {"name": "Kodiak", "radius": 80, "fa": ["KODIAK"]},
}

# Ryan Air villages and Kodiak-area villages (approximate). Used to fill gaps
# where no METAR station exists near a served community.
VILLAGES = {
    "Anvik": (62.66, -160.19), "Chuathbaluk": (61.57, -159.25), "Crooked Creek": (61.87, -158.11),
    "Grayling": (62.9, -160.07), "Holy Cross": (62.2, -159.77), "Kalskag": (61.54, -160.31),
    "Red Devil": (61.76, -157.31), "Russian Mission": (61.79, -161.32), "Shageluk": (62.68, -159.56),
    "Sleetmute": (61.7, -157.17), "Stony River": (61.78, -156.59),
    "Akiachak": (60.91, -161.43), "Akiak": (60.91, -161.21), "Atmautluak": (60.87, -162.27),
    "Chefornak": (60.16, -164.27), "Chevak": (61.53, -165.59), "Eek": (60.22, -162.02),
    "Goodnews Bay": (59.12, -161.59), "Hooper Bay": (61.53, -166.1), "Kasigluk": (60.89, -162.52),
    "Kipnuk": (59.94, -164.04), "Kongiganak": (59.96, -162.89), "Kwethluk": (60.81, -161.44),
    "Kwigillingok": (59.86, -163.13), "Marshall": (61.88, -162.08), "Mekoryuk": (60.39, -166.19),
    "Napakiak": (60.7, -161.96), "Napaskiak": (60.71, -161.77), "Nightmute": (60.48, -164.72),
    "Nunapitchuk": (60.9, -162.46), "Platinum": (59.01, -161.82), "Quinhagak": (59.75, -161.9),
    "Scammon Bay": (61.84, -165.58), "Toksook Bay": (60.53, -165.1), "Tuluksak": (61.1, -160.96),
    "Tuntutuliak": (60.34, -162.67), "Tununak": (60.58, -165.26),
    "Alakanuk": (62.69, -164.62), "Kotlik": (63.03, -163.55), "Nunam Iqua": (62.53, -164.85),
    "Ambler": (67.09, -157.86), "Buckland": (65.98, -161.12), "Deering": (66.08, -162.72),
    "Kiana": (66.97, -160.43), "Kivalina": (67.73, -164.53), "Kobuk": (66.91, -156.88),
    "Noatak": (67.57, -162.97), "Noorvik": (66.84, -161.03), "Point Hope": (68.35, -166.76),
    "Selawik": (66.6, -160.01), "Shungnak": (66.89, -157.14),
    "Brevig Mission": (65.33, -166.49), "Diomede": (65.76, -168.95), "Elim": (64.62, -162.26),
    "Gambell": (63.78, -171.74), "Golovin": (64.54, -163.03), "Savoonga": (63.69, -170.48),
    "Shishmaref": (66.26, -166.07), "Teller": (65.26, -166.36), "Wales": (65.61, -168.09),
    "White Mountain": (64.68, -163.41),
    "Mountain Village": (62.09, -163.72), "Pilot Station": (61.94, -162.88),
    "Koyuk": (64.93, -161.16), "Shaktoolik": (64.36, -161.2), "St. Michael": (63.48, -162.04),
    "Stebbins": (63.52, -162.29),
    "Old Harbor": (57.2, -153.3), "Larsen Bay": (57.54, -153.98), "Port Lions": (57.87, -152.88),
    "Ouzinkie": (57.92, -152.5), "Akhiok": (56.94, -154.17), "Karluk": (57.57, -154.45),
}

RADARS = [
    ("ALASKA", "Alaska mosaic"),
    ("PABC", "Bethel NEXRAD"),
    ("PAEC", "Nome NEXRAD"),
    ("PAKC", "King Salmon NEXRAD"),
    ("PAHG", "Kenai NEXRAD"),
]
SATELLITE = [
    ("https://cdn.star.nesdis.gov/GOES18/ABI/SECTOR/ak/GEOCOLOR/1000x1000.jpg", "GOES-West GeoColor, Alaska"),
    ("https://cdn.star.nesdis.gov/GOES18/ABI/SECTOR/ak/13/1000x1000.jpg", "GOES-West IR (Band 13), Alaska"),
]

CAT_ORDER = {"VFR": 0, "MVFR": 1, "IFR": 2, "LIFR": 3}
SOURCES = {}  # name -> "ok" | error text


# ---------------------------------------------------------------- utilities

def get(url, *, json_=True, headers=None, timeout=30, data=None, name=None):
    h = {"User-Agent": UA, "Accept": "application/json" if json_ else "*/*"}
    h.update(headers or {})
    req = urllib.request.Request(url, headers=h, data=data)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read()
        if name:
            SOURCES.setdefault(name, "ok")
        if not json_:
            return body
        return json.loads(body) if body.strip() else []
    except Exception as e:  # noqa: BLE001
        if name:
            SOURCES[name] = f"failed: {type(e).__name__}: {e}"[:200]
        return None


def awc(endpoint, **params):
    q = urllib.parse.urlencode({**params, "format": "json"})
    return get(f"https://aviationweather.gov/api/data/{endpoint}?{q}", name=f"aviationweather.gov/{endpoint}")


def nm(a, b):
    r = math.radians
    dlat, dlon = r(b[0] - a[0]), r(b[1] - a[1])
    x = math.sin(dlat / 2) ** 2 + math.cos(r(a[0])) * math.cos(r(b[0])) * math.sin(dlon / 2) ** 2
    return 3440.065 * 2 * math.asin(math.sqrt(x))


def num(v):
    if v is None:
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("+", "").strip()
    try:
        if " " in s:
            w, f = s.split()
            a, b = f.split("/")
            return float(w) + float(a) / float(b)
        if "/" in s:
            a, b = s.split("/")
            return float(a) / float(b)
        return float(s)
    except Exception:
        return None


def ceiling(clouds, vert_vis=None):
    bases = [c.get("base") for c in (clouds or []) if c.get("cover") in ("BKN", "OVC", "OVX") and c.get("base") is not None]
    if vert_vis is not None:
        bases.append(num(vert_vis) * 100 if num(vert_vis) and num(vert_vis) < 100 else num(vert_vis))
    return min(bases) if bases else None


def category(cig, vis):
    if (cig is not None and cig < 500) or (vis is not None and vis < 1):
        return "LIFR"
    if (cig is not None and cig < 1000) or (vis is not None and vis < 3):
        return "IFR"
    if (cig is not None and cig <= 3000) or (vis is not None and vis <= 5):
        return "MVFR"
    return "VFR"


def worst(*cats):
    cats = [c for c in cats if c]
    return max(cats, key=lambda c: CAT_ORDER[c]) if cats else None


def point_in_poly(lat, lon, coords):
    pts = [(float(c["lat"]), float(c["lon"])) for c in coords]
    inside = False
    j = len(pts) - 1
    for i in range(len(pts)):
        yi, xi = pts[i]
        yj, xj = pts[j]
        if ((yi > lat) != (yj > lat)) and (lon < (xj - xi) * (lat - yi) / ((yj - yi) or 1e-9) + xi):
            inside = not inside
        j = i
    return inside


def esc(s):
    return html.escape(str(s if s is not None else ""))


def local(ts):
    if ts is None:
        return "—"
    if isinstance(ts, (int, float)):
        t = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    else:
        t = dt.datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return t.astimezone(AK_TZ).strftime("%a %H:%M %Z")


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------- collection

def collect_stations():
    """All METAR-reporting stations within each hub's radius."""
    info = awc("stationinfo", bbox="56,-172.8,69.5,-147.5") or []
    stations = {}
    for s in info:
        if "METAR" not in (s.get("siteType") or []):
            continue
        pos = (s["lat"], s["lon"])
        best = None
        for icao, h in HUBS.items():
            hub = next((x for x in info if x["icaoId"] == icao), None)
            if not hub:
                continue
            d = nm(pos, (hub["lat"], hub["lon"]))
            if d <= h["radius"] and (best is None or d < best[1]):
                best = (icao, d)
        if best or s["icaoId"] in HUBS:
            stations[s["icaoId"]] = {
                "id": s["icaoId"], "name": (s.get("site") or s["icaoId"]).replace(" Arpt", ""),
                "lat": s["lat"], "lon": s["lon"], "elev": s.get("elev"),
                "hub": s["icaoId"] if s["icaoId"] in HUBS else best[0],
                "dist": 0 if s["icaoId"] in HUBS else round(best[1]),
                "has_taf": "TAF" in (s.get("siteType") or []),
            }
    return stations


def collect_metars(ids):
    out = {}
    for c in chunks(ids, 60):
        for m in awc("metar", ids=",".join(c), hours=6) or []:
            out.setdefault(m["icaoId"], []).append(m)
    for k in out:
        out[k].sort(key=lambda m: m.get("obsTime") or 0, reverse=True)
    return out


def collect_tafs(ids):
    out = {}
    for c in chunks(ids, 60):
        for t in awc("taf", ids=",".join(c)) or []:
            out[t["icaoId"]] = t
    return out


def collect_airports(ids):
    out = {}
    for c in chunks(ids, 60):
        for a in awc("airport", ids=",".join(c)) or []:
            out[a["icaoId"]] = a
    return out


def nws_latest(ptype, office=None, limit=1, want=None):
    lst = get(f"https://api.weather.gov/products/types/{ptype}", name=f"api.weather.gov/{ptype}")
    if not lst:
        return []
    items = [p for p in lst.get("@graph", []) if not office or p.get("issuingOffice") == office]
    texts = []
    for p in items[: (want or limit)]:
        d = get(p["@id"], name=f"api.weather.gov/{ptype}")
        if d:
            texts.append({"time": d.get("issuanceTime"), "text": d.get("productText", "")})
    return texts


def collect_aawu():
    """Alaska area forecasts (FA8/FA9), AIRMETs (WA8/WA9) and SIGMETs (SIG, WSV)."""
    fa, seen = [], set()
    for t in ("FA8", "FA9"):
        for p in nws_latest(t, "PAWU", want=14):
            hdr = re.search(r"^(FA\d[A-Z])\s*$", p["text"], re.M)
            key = hdr.group(1) if hdr else p["time"]
            if key in seen:
                continue
            seen.add(key)
            fa.append(p)
    wa = []
    for t in ("WA8", "WA9"):
        wa += nws_latest(t, "PAWU", want=1)
    sig = [p for p in nws_latest("SIG", "PAWU", want=6) + nws_latest("WSV", "PAWU", want=3)]
    now = dt.datetime.now(dt.timezone.utc)
    active = []
    for p in sig:
        m = re.search(r"VALID (\d{6})/(\d{6})", p["text"])
        if m and "CNL" not in p["text"]:
            end = m.group(2)
            try:
                e = now.replace(day=int(end[:2]), hour=int(end[2:4]), minute=int(end[4:]), second=0, microsecond=0)
                if e < now - dt.timedelta(hours=1):
                    continue
            except ValueError:
                pass
            active.append(p)
    return fa, wa, active


def parse_fa_zones(fa_products):
    zones, synopsis = {}, []
    for p in fa_products:
        t = p["text"]
        m = re.search(r"SYNOPSIS VALID UNTIL \d+\n(.*?)\n\.", t, re.S)
        if m:
            synopsis.append(" ".join(m.group(1).split()))
        for block in re.split(r"\n\.\n", t):
            hm = re.match(r"\s*(.{3,70}?)\.\.\.VALID UNTIL (\d+)", block)
            if hm:
                zones[hm.group(1).strip()] = block.strip()
    return zones, list(dict.fromkeys(synopsis))


def collect_afd():
    out = {}
    for office in ("AFC", "AFG"):
        lst = get(f"https://api.weather.gov/products/types/AFD/locations/{office}", name="api.weather.gov/AFD")
        if lst and lst.get("@graph"):
            d = get(lst["@graph"][0]["@id"], name="api.weather.gov/AFD")
            if d:
                out[office] = {"time": d.get("issuanceTime"), "text": d.get("productText", "")}
    return out


def afd_sections(text):
    secs = {}
    for m in re.finditer(r"^\.([A-Z][A-Z /\-&()0-9]+?)\.\.\.(.*?)(?=^\.[A-Z][A-Z /\-&()0-9]+?\.\.\.|^&&|\Z)", text, re.M | re.S):
        secs[m.group(1).strip()] = m.group(2).strip()
    return secs


def collect_alerts():
    d = get("https://api.weather.gov/alerts/active?area=AK", name="api.weather.gov/alerts") or {}
    return [f["properties"] for f in d.get("features", [])]


def collect_openmeteo(points):
    """points: list of (key, lat, lon). Returns key -> hourly dict (next 12h + past 6h)."""
    out = {}
    hourly = "temperature_2m,dew_point_2m,precipitation,snowfall,weather_code,cloud_cover_low,visibility,wind_speed_10m,wind_direction_10m,wind_gusts_10m,freezing_level_height"
    for c in chunks(points, 40):
        q = urllib.parse.urlencode({
            "latitude": ",".join(f"{p[1]:.3f}" for p in c),
            "longitude": ",".join(f"{p[2]:.3f}" for p in c),
            "hourly": hourly, "wind_speed_unit": "kn", "timezone": "GMT",
            "past_hours": 6, "forecast_hours": 12,
        })
        d = get(f"https://api.open-meteo.com/v1/forecast?{q}", name="open-meteo.com")
        if d is None:
            continue
        if isinstance(d, dict):
            d = [d]
        for p, r in zip(c, d):
            out[p[0]] = r.get("hourly", {})
    return out


def collect_nws_point(lat, lon):
    pt = get(f"https://api.weather.gov/points/{lat:.4f},{lon:.4f}", name="api.weather.gov/points")
    if not pt:
        return None
    fc = get(pt["properties"]["forecast"], name="api.weather.gov/points")
    if not fc:
        return None
    p = fc["properties"]["periods"][0]
    return f"{p['name']}: {p['detailedForecast']}"


def collect_wu(lat, lon, key):
    near = get(f"https://api.weather.com/v3/location/near?geocode={lat},{lon}&product=pws&format=json&apiKey={key}", name="weather underground")
    try:
        sid = near["location"]["stationId"][0]
        dist = near["location"]["distanceMi"][0]
    except Exception:
        return None
    if dist > 10:
        return None
    obs = get(f"https://api.weather.com/v2/pws/observations/current?stationId={sid}&format=json&units=e&apiKey={key}", name="weather underground")
    try:
        o = obs["observations"][0]
        i = o["imperial"]
        return f"WU PWS {sid} ({dist:.1f} mi): {i['temp']}°F, wind {o.get('winddir')}° {i['windSpeed']} G{i['windGust']} mph, precip rate {i['precipRate']} in/h"
    except Exception:
        return None


def collect_notams(ids):
    """FAA NOTAM API if credentials are set. Returns (dict id -> list[str], status)."""
    cid, sec = os.environ.get("FAA_CLIENT_ID"), os.environ.get("FAA_CLIENT_SECRET")
    if not (cid and sec):
        SOURCES["FAA NOTAM API"] = "not configured (set FAA_CLIENT_ID / FAA_CLIENT_SECRET)"
        return {}, "unconfigured"
    out = {}

    def one(icao):
        d = get(f"https://external-api.faa.gov/notamapi/v1/notams?icaoLocation={icao}&pageSize=200",
                headers={"client_id": cid, "client_secret": sec}, name="FAA NOTAM API")
        items = []
        for it in (d or {}).get("items", []):
            core = it.get("properties", {}).get("coreNOTAMData", {}).get("notam", {})
            txt = core.get("text") or ""
            num_ = core.get("number") or ""
            items.append(f"{num_} {txt}".strip())
        return icao, items

    with cf.ThreadPoolExecutor(8) as ex:
        for icao, items in ex.map(one, ids):
            out[icao] = items
    return out, "ok"


def classify_notams(items):
    ficon, rwy, other = [], [], []
    for n in items:
        u = n.upper()
        if "FICON" in u or re.search(r"\bRWYCC\b|\b[0-6]/[0-6]/[0-6]\b", u):
            ficon.append(n)
        elif re.search(r"\bRWY\b|\bAD AP CLSD\b|\bAD CLSD\b|\bLGT\b|\bPAPI\b|\bREIL\b", u):
            rwy.append(n)
        elif re.search(r"\bNAV\b|\bIAP\b|\bRNAV\b|\bGPS\b|\bOBST\b|\bSVC\b|\bAWOS\b|\bASOS\b|\bFUEL\b", u):
            other.append(n)
    return ficon, rwy, other


# ---------------------------------------------------------------- analysis

def taf_window(taf, start, end):
    """Worst category, max wind/gust, and weather strings within [start, end] epoch secs."""
    res = {"cat": None, "wspd": 0, "wgst": 0, "wx": set(), "lines": []}
    if not taf:
        return res
    for f in taf.get("fcsts", []):
        if f["timeTo"] < start or f["timeFrom"] > end:
            continue
        c = category(ceiling(f.get("clouds"), f.get("vertVis")), num(f.get("visib")))
        res["cat"] = worst(res["cat"], c)
        res["wspd"] = max(res["wspd"], f.get("wspd") or 0)
        res["wgst"] = max(res["wgst"], f.get("wgst") or 0)
        if f.get("wxString"):
            res["wx"].add(f["wxString"])
        if f.get("wshearHgt"):
            res["wx"].add(f"LLWS {f['wshearHgt']*100}ft")
    return res


def model_window(h):
    """Summarise Open-Meteo hourly data for the next 12h."""
    if not h or not h.get("time"):
        return None
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    idx = [i for i, t in enumerate(h["time"]) if dt.datetime.fromisoformat(t) >= now - dt.timedelta(minutes=59)]
    past = [i for i, t in enumerate(h["time"]) if dt.datetime.fromisoformat(t) < now]
    if not idx:
        return None
    pick = lambda k: [h[k][i] for i in idx if h[k][i] is not None]  # noqa: E731
    vis_m = pick("visibility")
    codes = pick("weather_code")
    temps = pick("temperature_2m")
    return {
        "min_vis_sm": round(min(vis_m) / 1609.34, 1) if vis_m else None,
        "max_gust": round(max(pick("wind_gusts_10m") or [0])),
        "max_wind": round(max(pick("wind_speed_10m") or [0])),
        "low_cloud_hours": sum(1 for v in pick("cloud_cover_low") if v >= 80),
        "precip_hours": sum(1 for v in pick("precipitation") if v >= 0.2),
        "freezing_precip": any(c in (56, 57, 66, 67) for c in codes),
        "snow": any(c in (71, 73, 75, 77, 85, 86) for c in codes),
        "thunder": any(c >= 95 for c in codes),
        "fog": any(c in (45, 48) for c in codes),
        "min_temp": min(temps) if temps else None,
        "max_temp": max(temps) if temps else None,
        "fzlvl_ft": round(min(pick("freezing_level_height") or [99999]) * 3.281, -2),
        "past_precip_mm": round(sum(h["precipitation"][i] or 0 for i in past), 1),
        "past_snow_cm": round(sum(h["snowfall"][i] or 0 for i in past), 1),
        "past_min_temp": min([h["temperature_2m"][i] for i in past if h["temperature_2m"][i] is not None] or [None]) if past else None,
    }


def crosswind(wdir, wspd, runways):
    if wdir in (None, "VRB") or not wspd or not runways:
        return None, None
    best = None
    for r in runways:
        a = r.get("alignment")
        if a is None:
            continue
        ang = math.radians(float(wdir) - float(a))
        xw = abs(wspd * math.sin(ang))
        if best is None or xw < best[0]:
            best = (round(xw), r["id"])
    return best if best else (None, None)


def runway_condition(airport, metars, model, ficon):
    surf_names = {"A": "asphalt", "C": "concrete", "G": "gravel", "T": "turf", "W": "water", "D": "dirt", "S": "snow"}
    rw = (airport or {}).get("runways") or []
    surfaces = ", ".join(f"{r['id']} {r.get('dimension','')} {surf_names.get(r.get('surface'), r.get('surface') or '?')}" for r in rw)
    if ficon:
        return {"level": "reported", "text": "FICON reported. See NOTAM.", "detail": ficon, "surfaces": surfaces}
    wx_recent = " ".join((m.get("wxString") or "") for m in metars[:6])
    temp_now = metars[0].get("temp") if metars else None
    gravel = any(r.get("surface") in ("G", "D", "T") for r in rw)
    level, text = "ok", "Likely dry"
    precip = bool(re.search(r"RA|SN|DZ|PL|GR|GS|UP|SG", wx_recent)) or (model and model["past_precip_mm"] >= 1)
    if model and model["past_snow_cm"] >= 1 or re.search(r"SN|SG|PL", wx_recent):
        level, text = "bad", "Possible snow/slush contamination"
    elif re.search(r"FZRA|FZDZ", wx_recent) or (model and model["freezing_precip"]):
        level, text = "bad", "Possible ice (freezing precipitation)"
    elif precip and temp_now is not None and temp_now <= 1:
        level, text = "warn", "Wet with temperatures near freezing, so ice patches are possible"
    elif precip:
        level, text = "warn", "Likely wet" + (". Gravel may be soft or muddy" if gravel else "")
    elif temp_now is not None and metars and temp_now - (metars[0].get("dewp") or -99) <= 1 and temp_now <= 0:
        level, text = "warn", "Frost possible (temperature and dew point near freezing)"
    return {"level": level, "text": text + " (inferred from weather, not a FICON report)", "detail": [], "surfaces": surfaces}


def score_station(st, metars, taf, model, airport, hazards_here, pireps_near, start, end):
    score, reasons = 100, []
    m = metars[0] if metars else None
    now_cat, age_h = None, None
    if m:
        age_h = (dt.datetime.now(dt.timezone.utc).timestamp() - m["obsTime"]) / 3600
        now_cat = m.get("fltCat") or category(ceiling(m.get("clouds")), num(m.get("visib")))
        pen = {"VFR": 0, "MVFR": 15, "IFR": 35, "LIFR": 55}[now_cat]
        if pen:
            score -= pen
            reasons.append(f"Currently {now_cat}")
        if age_h > 2:
            score -= 5
            reasons.append(f"Observation is {age_h:.0f}h old")
        spread = (m.get("temp") or 0) - (m.get("dewp") if m.get("dewp") is not None else -99)
        if spread <= 2 and (m.get("wspd") or 0) <= 6 and now_cat in ("VFR", "MVFR"):
            score -= 8
            reasons.append(f"Temp/dew point spread {spread:.0f}°C, so fog is possible")
    tw = taf_window(taf, start, end)
    fc_cat = tw["cat"]
    if not fc_cat and model:
        vis = model["min_vis_sm"]
        # Low cloud cover (<~6,500 ft) isn't a ceiling height, so treat it as MVFR at most.
        fc_cat = category(2500 if model["low_cloud_hours"] >= 4 else None, vis)
    if fc_cat and CAT_ORDER[fc_cat] > CAT_ORDER.get(now_cat or "VFR", 0):
        pen = {"VFR": 0, "MVFR": 10, "IFR": 25, "LIFR": 40}[fc_cat]
        score -= pen
        reasons.append(f"{'TAF' if tw['cat'] else 'Model'} lowers to {fc_cat} today")
    wind = max((m or {}).get("wspd") or 0, tw["wspd"], (model or {}).get("max_wind", 0))
    gust = max((m or {}).get("wgst") or 0, tw["wgst"], (model or {}).get("max_gust", 0) if not taf else 0)
    if gust >= 35 or wind >= 30:
        score -= 40
        reasons.append(f"Strong wind, up to {max(wind, gust)} kt")
    elif gust >= 25 or wind >= 20:
        score -= 18
        reasons.append(f"Gusty wind, up to {max(wind, gust)} kt")
    xw, xrwy = crosswind((m or {}).get("wdir"), max((m or {}).get("wspd") or 0, (m or {}).get("wgst") or 0), (airport or {}).get("runways"))
    if xw is not None:
        if xw >= 20:
            score -= 30
            reasons.append(f"Crosswind {xw} kt on best runway {xrwy}")
        elif xw >= 15:
            score -= 15
            reasons.append(f"Crosswind {xw} kt on best runway {xrwy}")
    wx = " ".join([(m or {}).get("wxString") or ""] + list(tw["wx"]))
    if re.search(r"FZRA|FZDZ", wx) or (model and model["freezing_precip"]):
        score -= 35
        reasons.append("Freezing precipitation")
    if re.search(r"\bTS", wx) or (model and model["thunder"]):
        score -= 25
        reasons.append("Thunderstorms")
    if "LLWS" in wx:
        score -= 10
        reasons.append("Low-level wind shear forecast")
    if model and model["snow"] and not re.search(r"SN", wx):
        score -= 8
        reasons.append("Model shows snow")
    if model and model["min_temp"] is not None and -12 <= model["min_temp"] <= 2 and model["precip_hours"] >= 2:
        score -= 12
        reasons.append("Icing risk: precipitation with near-freezing temperatures")
    for hz in hazards_here:
        score -= hz["penalty"]
        reasons.append(hz["label"])
    for p in pireps_near:
        score -= 10 if re.search(r"(?<!LGT-)MOD|SEV", p["what"]) else 5
        reasons.append(f"PIREP {p['what']} ({p['dist']} nm)")
    score = max(0, min(100, score))
    rating = "GOOD" if score >= 75 else "MARGINAL" if score >= 50 else "POOR" if score >= 25 else "NO-GO"
    return {
        "score": score, "rating": rating, "reasons": reasons or ["No significant issues found"],
        "now_cat": now_cat, "fc_cat": fc_cat, "wind": wind, "gust": gust, "xw": xw, "xrwy": xrwy, "age_h": age_h,
    }


def hazards_for(lat, lon, fa_zone_hits, gairmets, sigmets_geo):
    out = []
    for s in sigmets_geo:
        if s.get("coords") and point_in_poly(lat, lon, s["coords"]):
            out.append({"label": f"Inside SIGMET: {s.get('hazard')}", "penalty": 30})
    for g in gairmets:
        if g.get("coords") and point_in_poly(lat, lon, g["coords"]):
            out.append({"label": f"G-AIRMET {g.get('hazard')} {g.get('severity') or ''}".strip(), "penalty": 10})
    # AAWU AIRMETs: ignore high-altitude turbulence (irrelevant to bush ops),
    # weight by type, dedupe, and cap the combined penalty.
    weights = {"IFR": 10, "ICE": 10, "TURB": 8, "STG SFC WND": 8, "LLWS": 6, "MT OBSC": 3}
    seen, total = set(), 0
    for label in fa_zone_hits:
        if label in seen or re.search(r"FL(2[5-9]|[3-4]\d)\d", label) and "BLW" not in label:
            continue
        seen.add(label)
        kind = re.match(r"AIRMET ([A-Z .]+?):", label)
        w = weights.get(kind.group(1) if kind else "", 5)
        if "OFSHR" in label and kind and kind.group(1) == "IFR":
            w = 3
        w = min(w, max(0, 20 - total))
        total += w
        out.append({"label": label, "penalty": w})
    return out


def zone_airmets(zone_text):
    hits = []
    for m in re.finditer(r"\*\*\*AIRMET ([A-Z .]+)\*\*\*(.*?)(?=\*\*\*|\n\.\.\.|\Z)", zone_text, re.S):
        hits.append(f"AIRMET {m.group(1).strip()}: {' '.join(m.group(2).split())[:140]}")
    return hits


def pressure_analysis(latest):
    rows = []
    for icao, m in latest.items():
        p = m.get("slp") or m.get("altim")
        if p:
            tend = None
            tm = re.search(r"\b5(\d)(\d{3})\b", m.get("rawOb", "").split("RMK")[-1])
            if tm:
                sign = -1 if tm.group(1) in "5678" else 1
                tend = sign * int(tm.group(2)) / 10
            rows.append((icao, float(p), tend))
    if not rows:
        return None
    lo = min(rows, key=lambda r: r[1])
    hi = max(rows, key=lambda r: r[1])
    tends = [r for r in rows if r[2] is not None]
    fall = min(tends, key=lambda r: r[2]) if tends else None
    rise = max(tends, key=lambda r: r[2]) if tends else None
    return {"low": lo, "high": hi, "fall": fall, "rise": rise, "gradient": round(hi[1] - lo[1], 1), "n": len(rows)}


# ---------------------------------------------------------------- AI narrative

AI_INSTRUCTIONS = """You are an Alaska bush-operations dispatcher meteorologist writing the morning briefing for Ryan Air (Part 135 cargo/passenger carrier, Cessna 207/208, PC-12, CASA 212, Saab 340; mostly day VFR with some IFR capability).

Write the briefing sections below in plain HTML fragments (use only <h3>, <p>, <ul>, <li>, <strong>). No preamble, no markdown, no code fences.
1. <h3>Synoptic picture</h3>: the weather systems affecting western Alaska and Kodiak/Cook Inlet today: lows and fronts, their movement, and the pressure gradient. Say what the radar images show and where.
2. <h3>Flyability by region</h3>: one bullet per hub (Aniak, Bethel, St. Mary's, Emmonak, Unalakleet, Nome, Kotzebue, Anchorage, Kodiak). Give the go/marginal/poor call for the morning and afternoon and the main limiting factor. Mention villages that stand out.
3. <h3>Hazards to watch</h3>: icing, turbulence, wind/crosswind, visibility, runway surface concerns and SIGMET/AIRMET areas.
4. <h3>Best windows</h3>: the best times to launch, and which routes to hold or re-sequence.
Be concrete and brief (under 450 words). Base everything only on the data given. If data is missing, say so; don't invent it."""


def ai_data(summary_json, afd_text, fa_synopsis):
    return f"""AAWU area forecast synopsis:
{fa_synopsis}

NWS forecast discussion excerpts:
{afd_text[:6000]}

Computed station data (JSON):
{summary_json[:24000]}
"""


def clean_html(txt):
    return re.sub(r"^```(?:html)?|```$", "", txt, flags=re.M).strip()


def ai_narrative_api(summary_json, afd_text, fa_synopsis, radar_files):
    """Claude API path (used in CI). Radar images are sent inline."""
    import base64
    import anthropic

    client = anthropic.Anthropic()
    content = []
    for f in radar_files:
        content.append({"type": "text", "text": f"Radar image: {Path(f).stem}"})
        content.append({"type": "image", "source": {"type": "base64", "media_type": "image/gif",
                                                    "data": base64.standard_b64encode(Path(f).read_bytes()).decode()}})
    content.append({"type": "text", "text": ai_data(summary_json, afd_text, fa_synopsis)})
    try:
        resp = client.beta.messages.create(
            model=os.environ.get("BRIEFING_MODEL", "claude-opus-5"),
            max_tokens=16000,
            thinking={"type": "adaptive"},
            output_config={"effort": "medium"},
            betas=["server-side-fallback-2026-07-01"],
            fallbacks="default",
            system=AI_INSTRUCTIONS,
            messages=[{"role": "user", "content": content}],
        )
    except anthropic.APIStatusError as e:
        return None, f"API error {e.status_code}: {e.message}"[:200]
    except anthropic.APIConnectionError as e:
        return None, f"API connection error: {e}"[:200]
    if resp.stop_reason == "refusal":
        return None, "model declined the request"
    txt = "".join(b.text for b in resp.content if b.type == "text")
    return (clean_html(txt), "ok") if txt.strip() else (None, f"empty response ({resp.stop_reason})")


def ai_narrative_cli(summary_json, afd_text, fa_synopsis, radar_files):
    """Local path: the Claude Code CLI reads the radar images from disk."""
    exe = shutil.which("claude") or str(Path.home() / ".local/share/mise/installs/claude/latest/claude")
    if not Path(exe).exists():
        return None, "claude CLI not found"
    imgs = "\n".join(f"- {p}" for p in radar_files)
    prompt = f"{AI_INSTRUCTIONS}\n\nFirst use the Read tool to look at these radar images:\n{imgs}\n\n{ai_data(summary_json, afd_text, fa_synopsis)}"
    try:
        r = subprocess.run([exe, "-p", prompt, "--allowedTools", "Read", "--add-dir", str(OUT)],
                           capture_output=True, text=True, timeout=420, cwd=str(OUT))
        txt = r.stdout.strip()
        if r.returncode != 0 or not txt:
            return None, f"claude exited {r.returncode}: {r.stderr.strip()[:200]}"
        return clean_html(txt), "ok"
    except Exception as e:  # noqa: BLE001
        return None, f"{type(e).__name__}: {e}"


def ai_narrative(*a):
    if os.environ.get("ANTHROPIC_API_KEY"):
        return ai_narrative_api(*a)
    return ai_narrative_cli(*a)


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-ai", action="store_true", help="skip the Claude-written narrative")
    ap.add_argument("--open", action="store_true", help="open the briefing in the browser when done")
    args = ap.parse_args()

    OUT.mkdir(exist_ok=True)
    (OUT / "img").mkdir(exist_ok=True)
    (OUT / "archive").mkdir(exist_ok=True)
    now = dt.datetime.now(dt.timezone.utc)
    start, end = now.timestamp(), (now + dt.timedelta(hours=12)).timestamp()

    print("• stations…", flush=True)
    stations = collect_stations()
    ids = sorted(stations)

    print(f"• {len(ids)} stations: METAR/TAF/airports/hazards…", flush=True)
    with cf.ThreadPoolExecutor(12) as ex:
        f_metar = ex.submit(collect_metars, ids)
        f_taf = ex.submit(collect_tafs, [i for i in ids if stations[i]["has_taf"]])
        f_apt = ex.submit(collect_airports, ids)
        f_pirep = ex.submit(awc, "pirep", bbox="55,-175,70,-145", age=12)
        f_gair = ex.submit(awc, "gairmet")
        f_isig = ex.submit(awc, "isigmet")
        f_aawu = ex.submit(collect_aawu)
        f_afd = ex.submit(collect_afd)
        f_alert = ex.submit(collect_alerts)
        f_notam = ex.submit(collect_notams, ids)
        radar_files = []
        def dl(url, dest):
            b = get(url, json_=False, name="radar.weather.gov (NEXRAD)")
            if b:
                dest.write_bytes(b)
                return str(dest)
        f_radar = [ex.submit(dl, f"https://radar.weather.gov/ridge/standard/{r}_0.gif", OUT / "img" / f"{r}.gif") for r, _ in RADARS]

    metars, tafs, airports = f_metar.result(), f_taf.result(), f_apt.result()
    pireps = f_pirep.result() or []
    gairmets = [g for g in (f_gair.result() or []) if g.get("coords")]
    isig = [s for s in (f_isig.result() or []) if str(s.get("firId", "")).startswith("PA")]
    fa, wa, aawu_sig = f_aawu.result()
    afd = f_afd.result()
    alerts = f_alert.result()
    notams, notam_status = f_notam.result()
    radar_files = [f.result() for f in f_radar if f.result()]

    # Villages with no METAR station within 8 nm get a model forecast.
    gap_villages = []
    for v, pos in VILLAGES.items():
        if not any(nm(pos, (s["lat"], s["lon"])) <= 8 for s in stations.values()):
            hub = min(HUBS, key=lambda h: nm(pos, (stations[h]["lat"], stations[h]["lon"])) if h in stations else 1e9)
            gap_villages.append({"name": v, "lat": pos[0], "lon": pos[1], "hub": hub,
                                 "dist": round(nm(pos, (stations[hub]["lat"], stations[hub]["lon"]))) if hub in stations else None})

    print(f"• model forecast for {len(ids)} stations + {len(gap_villages)} unobserved villages…", flush=True)
    pts = [(i, stations[i]["lat"], stations[i]["lon"]) for i in ids] + [(v["name"], v["lat"], v["lon"]) for v in gap_villages]
    model = collect_openmeteo(pts)

    wu_key = os.environ.get("WU_API_KEY")
    if not wu_key:
        SOURCES["weather underground"] = "not configured (set WU_API_KEY)"
    print("• NWS point forecasts / WU for unobserved villages…", flush=True)
    with cf.ThreadPoolExecutor(8) as ex:
        nws_pts = list(ex.map(lambda v: collect_nws_point(v["lat"], v["lon"]), gap_villages))
        wu_pts = list(ex.map(lambda v: collect_wu(v["lat"], v["lon"], wu_key), gap_villages)) if wu_key else [None] * len(gap_villages)
    for v, n_, w_ in zip(gap_villages, nws_pts, wu_pts):
        v["nws"], v["wu"] = n_, w_

    zones, fa_synopsis = parse_fa_zones(fa)

    # PIREPs with moderate+ icing or turbulence.
    sig_pireps = []
    for p in pireps:
        what = []
        for k in ("icgInt1", "icgInt2"):
            if p.get(k) and re.search(r"MOD|SEV|HVY", p[k]):
                what.append(f"{p[k]} ICE")
        for k in ("tbInt1", "tbInt2"):
            if p.get(k) and re.search(r"MOD|SEV|EXTRM", p[k]):
                what.append(f"{p[k]} TURB")
        if what:
            sig_pireps.append({**p, "what": " / ".join(what)})

    # Per-station analysis.
    results = {}
    for i in ids:
        st = stations[i]
        hub = HUBS[st["hub"]]
        hit_zones = [z for z in zones if any(k in z for k in hub["fa"])]
        fa_hits = [a for z in hit_zones for a in zone_airmets(zones[z])]
        hz = hazards_for(st["lat"], st["lon"], fa_hits if i in HUBS else fa_hits[:2], gairmets, isig)
        near = []
        for p in sig_pireps:
            d = nm((st["lat"], st["lon"]), (p["lat"], p["lon"]))
            if d <= 50:
                near.append({"what": p["what"], "dist": round(d)})
        mw = model_window(model.get(i))
        ficon, rwy, other = classify_notams(notams.get(i, []))
        sc = score_station(st, metars.get(i, []), tafs.get(i), mw, airports.get(i), hz, near[:2], start, end)
        results[i] = {
            **st, **sc, "metar": (metars.get(i) or [{}])[0].get("rawOb"), "taf": (tafs.get(i) or {}).get("rawTAF"),
            "model": mw, "zones": hit_zones, "hazards": hz,
            "runway": runway_condition(airports.get(i), metars.get(i, []), mw, ficon),
            "notams": {"ficon": ficon, "rwy": rwy, "other": other, "total": len(notams.get(i, []))},
            "temp": (metars.get(i) or [{}])[0].get("temp"),
            "vis": num((metars.get(i) or [{}])[0].get("visib")),
            "cig": ceiling((metars.get(i) or [{}])[0].get("clouds")),
            "wx": (metars.get(i) or [{}])[0].get("wxString"),
            "obs": (metars.get(i) or [{}])[0].get("obsTime"),
        }

    for v in gap_villages:
        mw = model_window(model.get(v["name"]))
        hub = HUBS[v["hub"]]
        pseudo = {"lat": v["lat"], "lon": v["lon"]}
        hz = hazards_for(v["lat"], v["lon"], [], gairmets, isig)
        sc = score_station(pseudo, [], None, mw, None, hz, [], start, end)
        v.update(sc)
        v["model"] = mw

    latest = {i: metars[i][0] for i in metars if metars[i]}
    pa = pressure_analysis(latest)

    # Weather systems text (rule-based).
    precip_now = sorted({f"{stations[i]['name']} ({m.get('wxString')})" for i, m in latest.items()
                         if i in stations and m.get("wxString") and re.search(r"RA|SN|DZ|TS|PL|GR", m["wxString"])})
    afd_ex = []
    for off, d in afd.items():
        secs = afd_sections(d["text"])
        for k, v in secs.items():
            if re.search(r"SYNOPSIS|ANALYSIS|SHORT TERM|AVIATION|KEY MESSAGES|DISCUSSION", k):
                afd_ex.append({"office": "Anchorage (AFC)" if off == "AFC" else "Fairbanks (AFG)", "title": k, "text": v, "time": d["time"]})

    ai_html, ai_status = None, "skipped (--no-ai)"
    if not args.no_ai:
        print("• writing AI synoptic/flyability narrative with Claude…", flush=True)
        compact = {
            "generated_utc": now.isoformat(timespec="minutes"),
            "hubs": {i: {k: results[i][k] for k in ("name", "score", "rating", "reasons", "metar", "taf", "runway")} for i in HUBS if i in results},
            "villages": [{k: results[i][k] for k in ("name", "hub", "score", "rating", "reasons")} for i in ids if i not in HUBS],
            "unobserved_villages": [{k: v[k] for k in ("name", "hub", "score", "rating", "reasons")} for v in gap_villages],
            "pressure": pa, "precip_reported": precip_now,
            "airmets": [a for z in zones.values() for a in zone_airmets(z)][:40],
            "sigmets": [p["text"][:600] for p in aawu_sig],
            "pireps": [p["rawOb"] for p in sig_pireps][:20],
            "alerts": [f"{a['event']}: {a['areaDesc'][:120]}" for a in alerts][:20],
        }
        afd_txt = "\n\n".join(f"[{a['office']} {a['title']}]\n{a['text']}" for a in afd_ex)
        ai_html, ai_status = ai_narrative(json.dumps(compact, default=str), afd_txt, "\n".join(fa_synopsis), radar_files)
    SOURCES["Claude narrative"] = ai_status
    print(f"  narrative: {ai_status}", flush=True)

    page = render(now, results, gap_villages, zones, fa_synopsis, wa, aawu_sig, isig, gairmets, sig_pireps,
                  alerts, afd_ex, pa, precip_now, ai_html, notam_status)
    path = OUT / "briefing.html"
    path.write_text(page)
    stamp = now.astimezone(AK_TZ).strftime("%Y-%m-%d")
    (OUT / "archive" / f"briefing-{stamp}.html").write_text(page)
    (OUT / "index.html").write_text(page)  # served at the site root when deployed
    print(f"✓ wrote {path}")
    if args.open:
        subprocess.Popen(["xdg-open", str(path)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ---------------------------------------------------------------- rendering

def badge(rating):
    cls = {"GOOD": "good", "MARGINAL": "marg", "POOR": "poor", "NO-GO": "nogo"}.get(rating, "")
    return f'<span class="badge {cls}">{esc(rating)}</span>'


def catpill(c):
    return f'<span class="cat {esc((c or "na").lower())}">{esc(c or "—")}</span>'


def fmt_cig(c):
    return "—" if c is None else f"{int(c):,}"


def render(now, R, gaps, zones, fa_syn, wa, aawu_sig, isig, gairmets, pireps, alerts, afd_ex, pa, precip_now, ai_html, notam_status):
    ts = now.astimezone(AK_TZ).strftime("%A %B %-d, %Y · %H:%M %Z")
    hub_cards, hub_details = [], []
    for icao, h in HUBS.items():
        r = R.get(icao)
        if not r:
            hub_cards.append(f'<div class="hub"><div class="hub-top"><b>{esc(h["name"])}</b><span class="mono">{icao}</span></div><p class="muted">No data</p></div>')
            continue
        vill = [x for x in R.values() if x["hub"] == icao and x["id"] != icao]
        gv = [g for g in gaps if g["hub"] == icao]
        allv = [x["rating"] for x in vill] + [g["rating"] for g in gv]
        counts = {k: allv.count(k) for k in ("GOOD", "MARGINAL", "POOR", "NO-GO")}
        dist = "".join(f'<span class="dot {c}"></span>{n}&nbsp;' for c, n in zip(("good", "marg", "poor", "nogo"), counts.values()) if n)
        hub_cards.append(f"""
        <a class="hub r-{esc(r['rating'].lower())}" href="#{icao}">
          <div class="hub-top"><b>{esc(h['name'])}</b><span class="mono">{icao}</span></div>
          <div class="score">{r['score']}</div>
          {badge(r['rating'])} {catpill(r['now_cat'])} <span class="muted small">→ {esc(r['fc_cat'] or '—')}</span>
          <p class="small">{esc(r['reasons'][0])}</p>
          <div class="small muted">Villages: {dist or '—'}</div>
          <div class="small rw rw-{r['runway']['level']}">Runway: {esc(r['runway']['text'].split(' (')[0])}</div>
        </a>""")

        rows = []
        for x in sorted(vill, key=lambda x: x["dist"]):
            rows.append(f"""<tr>
              <td><b>{esc(x['name'])}</b><br><span class="mono muted">{x['id']} · {x['dist']} nm</span></td>
              <td>{catpill(x['now_cat'])}</td><td>{catpill(x['fc_cat'])}</td>
              <td class="mono">{fmt_cig(x['cig'])}</td><td class="mono">{'' if x['vis'] is None else f"{x['vis']:g}"}</td>
              <td class="mono">{x['wind'] or 0}{f"G{x['gust']}" if x['gust'] else ''}{f" / X{x['xw']}" if x['xw'] is not None else ''}</td>
              <td class="rw-{x['runway']['level']} small">{esc(x['runway']['text'].split(' (')[0])}<br><span class="muted">{esc(x['runway']['surfaces'])}</span></td>
              <td>{badge(x['rating'])}<br><span class="small muted">{esc('; '.join(x['reasons'][:3]))}</span></td>
            </tr>""")
        for g in gv:
            m = g.get("model") or {}
            extra = " · ".join(filter(None, [g.get("nws"), g.get("wu")]))
            rows.append(f"""<tr class="gap">
              <td><b>{esc(g['name'])}</b><br><span class="mono muted">no METAR · model</span></td>
              <td>—</td><td>{catpill(g['fc_cat'])}</td><td class="mono">{m.get('low_cloud_hours', '—')}h low cld</td>
              <td class="mono">{m.get('min_vis_sm', '—')}</td><td class="mono">{m.get('max_wind', 0)}G{m.get('max_gust', 0)}</td>
              <td class="small muted">past 6h precip {m.get('past_precip_mm', '—')} mm</td>
              <td>{badge(g['rating'])}<br><span class="small muted">{esc('; '.join(g['reasons'][:3]))}</span>{f'<details><summary class="small">NWS / WU</summary><p class="small">{esc(extra)}</p></details>' if extra else ''}</td>
            </tr>""")

        n = r["notams"]
        if notam_status == "ok":
            notam_html = (f"<p class='small muted'>{n['total']} active NOTAMs</p>"
                          + "".join(f"<pre class='notam ficon'>{esc(t)}</pre>" for t in n["ficon"])
                          + "".join(f"<pre class='notam'>{esc(t)}</pre>" for t in n["rwy"][:12])
                          + "".join(f"<pre class='notam dim'>{esc(t)}</pre>" for t in n["other"][:8]))
        else:
            notam_html = f"<p class='small muted'>NOTAM feed isn't configured. <a href='https://notams.aim.faa.gov/notamSearch/nsapp.html#/results?searchType=0&designatorsForLocation={icao}' target='_blank'>Open FAA NOTAM search for {icao} ↗</a></p>"
        zone_html = "".join(f"<details><summary>{esc(z)}</summary><pre>{esc(zones[z])}</pre></details>" for z in r["zones"])
        m = r["model"] or {}
        hub_details.append(f"""
        <section class="hubd" id="{icao}">
          <header><h3>{esc(h['name'])} <span class="mono muted">{icao}</span></h3>{badge(r['rating'])} <span class="score-sm">{r['score']}/100</span></header>
          <div class="grid2">
            <div>
              <h4>Observation <span class="muted small">{esc(local(r['obs']))}</span></h4><pre>{esc(r['metar'] or 'No METAR')}</pre>
              <h4>TAF</h4><pre>{esc(r['taf'] or 'No TAF. Model guidance used.')}</pre>
              <h4>Model next 12h</h4>
              <p class="small mono">min vis {m.get('min_vis_sm','—')} SM · max wind {m.get('max_wind','—')}G{m.get('max_gust','—')} kt · low cloud ≥80% for {m.get('low_cloud_hours','—')}h · precip {m.get('precip_hours','—')}h · temp {m.get('min_temp','—')}…{m.get('max_temp','—')}°C · freezing level {m.get('fzlvl_ft','—')} ft</p>
            </div>
            <div>
              <h4>Flyability factors</h4><ul>{''.join(f'<li>{esc(x)}</li>' for x in r['reasons'])}</ul>
              <h4>Runway condition</h4><p class="rw-{r['runway']['level']}">{esc(r['runway']['text'])}</p><p class="small muted">{esc(r['runway']['surfaces'])}</p>
              <h4>NOTAMs</h4>{notam_html}
              {f'<h4>AAWU area forecast</h4>{zone_html}' if zone_html else ''}
            </div>
          </div>
          <div class="tablewrap"><table>
            <thead><tr><th>Station / village</th><th>Now</th><th>Today</th><th>Ceiling ft</th><th>Vis SM</th><th>Wind kt</th><th>Runway</th><th>Flyability</th></tr></thead>
            <tbody>{''.join(rows) or '<tr><td colspan=8 class="muted">No surrounding stations</td></tr>'}</tbody>
          </table></div>
        </section>""")

    radar_html = "".join(f"""<figure><img src="https://radar.weather.gov/ridge/standard/{r}_loop.gif" loading="lazy" alt="{esc(t)} radar loop" onerror="this.src='img/{r}.gif'"><figcaption>{esc(t)} <a href="https://radar.weather.gov/station/{r.lower()}/standard" target="_blank">↗</a></figcaption></figure>""" for r, t in RADARS)
    sat_html = "".join(f"""<figure><img src="{u}" loading="lazy" alt="{esc(t)}" onerror="this.closest('figure').remove()"><figcaption>{esc(t)}</figcaption></figure>""" for u, t in SATELLITE)
    pa_html = ""
    if pa:
        def nm_(i):
            return R[i]["name"] if i in R else i
        pa_html = f"""<ul>
          <li>Lowest pressure: <b>{esc(nm_(pa['low'][0]))}</b> {pa['low'][1]:.1f} hPa · highest: <b>{esc(nm_(pa['high'][0]))}</b> {pa['high'][1]:.1f} hPa. The {pa['gradient']} hPa spread across the region {'points to a tight gradient and strong winds' if pa['gradient'] >= 20 else 'is moderate' if pa['gradient'] >= 10 else 'is weak, so winds should be light'}.</li>
          {f"<li>Fastest falling: <b>{esc(nm_(pa['fall'][0]))}</b> {pa['fall'][2]:+.1f} hPa/3h{', a system approaching' if pa['fall'][2] <= -2 else ''}.</li>" if pa['fall'] else ''}
          {f"<li>Fastest rising: <b>{esc(nm_(pa['rise'][0]))}</b> {pa['rise'][2]:+.1f} hPa/3h{', clearing or building behind a system' if pa['rise'][2] >= 2 else ''}.</li>" if pa['rise'] else ''}
          <li>Stations reporting precipitation: {esc(', '.join(precip_now) or 'none')}</li>
        </ul>"""
    afd_html = "".join(f"<details {'open' if i < 2 else ''}><summary>{esc(a['office'])}: {esc(a['title'])} <span class='muted small'>{esc(local(a['time']))}</span></summary><pre>{esc(a['text'])}</pre></details>" for i, a in enumerate(afd_ex))
    sig_html = "".join(f"<pre class='notam ficon'>{esc(p['text'].strip()[:1200])}</pre>" for p in aawu_sig) \
        + "".join(f"<pre class='notam ficon'>{esc(s.get('rawSigmet') or s.get('hazard'))}</pre>" for s in isig) or "<p class='muted'>No active Alaska SIGMETs.</p>"
    wa_html = "".join(f"<details><summary>AIRMET bulletin {esc(local(p['time']))}</summary><pre>{esc(p['text'])}</pre></details>" for p in wa)
    pirep_html = "".join(f"<li class='mono small'>{esc(p['rawOb'])}</li>" for p in pireps[:25]) or "<li class='muted'>No moderate or greater icing or turbulence reports in the last 12 hours.</li>"
    relevant_alerts = [a for a in alerts if not re.search(r"Test", a.get("event", ""))]
    alert_html = "".join(f"<li><b>{esc(a['event'])}</b>: {esc(a['areaDesc'][:220])} <span class='muted small'>until {esc(local(a.get('ends') or a.get('expires')))}</span></li>" for a in relevant_alerts) or "<li class='muted'>No active NWS alerts for Alaska.</li>"
    src_html = "".join(f"<li><span class='mono'>{esc(k)}</span>: {'✅' if v == 'ok' else '⚠️ ' + esc(v)}</li>" for k, v in sorted(SOURCES.items()))

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Ryan Air Morning Briefing</title>
<style>
:root{{--bg:#f5f6f8;--card:#fff;--ink:#101828;--muted:#667085;--line:#e4e7ec;--good:#12805c;--marg:#b98900;--poor:#d0541b;--nogo:#c01d2e;--vfr:#12805c;--mvfr:#1d5fd1;--ifr:#c01d2e;--lifr:#a21caf;--accent:#d9731c}}
@media (prefers-color-scheme:dark){{:root:not([data-theme=light]){{--bg:#0b1118;--card:#121a24;--ink:#e6edf3;--muted:#8b98a8;--line:#223040;--good:#3fbf8a;--marg:#e0b53a;--poor:#f07d43;--nogo:#f0556a;--vfr:#3fbf8a;--mvfr:#5b9bff;--ifr:#f0556a;--lifr:#d77cf0}}}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--ink);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
.wrap{{max-width:1280px;margin:0 auto;padding:0 16px}}header.top{{padding:24px 0 8px}}h1{{margin:0;font-size:1.7rem}}h2{{margin:40px 0 12px;font-size:1.3rem;border-bottom:2px solid var(--accent);display:inline-block;padding-bottom:2px}}h3{{margin:0}}h4{{margin:14px 0 6px;font-size:.9rem;text-transform:uppercase;letter-spacing:.05em;color:var(--muted)}}
.muted{{color:var(--muted)}}.small{{font-size:.82rem}}.mono,pre{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace}}
pre{{white-space:pre-wrap;background:var(--bg);border:1px solid var(--line);border-radius:8px;padding:10px;font-size:.8rem;margin:0 0 6px;max-height:420px;overflow:auto}}
.hubs{{display:grid;grid-template-columns:repeat(auto-fill,minmax(210px,1fr));gap:12px}}
.hub{{display:block;background:var(--card);border:1px solid var(--line);border-top:5px solid var(--line);border-radius:12px;padding:12px 14px;color:inherit;text-decoration:none}}
.hub.r-good{{border-top-color:var(--good)}}.hub.r-marginal{{border-top-color:var(--marg)}}.hub.r-poor{{border-top-color:var(--poor)}}.hub.r-no-go{{border-top-color:var(--nogo)}}
.hub-top{{display:flex;justify-content:space-between;align-items:baseline}}.hub .score{{font-size:2.2rem;font-weight:800;line-height:1.1}}.hub p{{margin:6px 0}}
.badge{{display:inline-block;font-weight:700;font-size:.72rem;letter-spacing:.05em;padding:2px 8px;border-radius:6px;color:#fff;background:var(--muted)}}
.badge.good{{background:var(--good)}}.badge.marg{{background:var(--marg)}}.badge.poor{{background:var(--poor)}}.badge.nogo{{background:var(--nogo)}}
.cat{{display:inline-block;font:700 .72rem ui-monospace,monospace;padding:1px 6px;border-radius:5px;border:1.5px solid var(--muted);color:var(--muted)}}
.cat.vfr{{border-color:var(--vfr);color:var(--vfr)}}.cat.mvfr{{border-color:var(--mvfr);color:var(--mvfr)}}.cat.ifr{{border-color:var(--ifr);color:var(--ifr)}}.cat.lifr{{border-color:var(--lifr);color:var(--lifr)}}
.dot{{display:inline-block;width:8px;height:8px;border-radius:50%;margin-right:3px}}.dot.good{{background:var(--good)}}.dot.marg{{background:var(--marg)}}.dot.poor{{background:var(--poor)}}.dot.nogo{{background:var(--nogo)}}
.rw-ok{{color:var(--good)}}.rw-warn{{color:var(--marg)}}.rw-bad,.rw-reported{{color:var(--nogo)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}}
.ai h3{{margin:14px 0 6px;font-size:1.05rem}}.ai h3:first-child{{margin-top:0}}
.imgs{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}}figure{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}figure img{{width:100%;display:block}}figcaption{{padding:6px 10px;font-size:.82rem}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}.hubd{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:16px}}
.hubd>header{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}.score-sm{{font-weight:700}}
.tablewrap{{overflow-x:auto;margin-top:14px}}table{{width:100%;border-collapse:collapse;font-size:.85rem}}th,td{{text-align:left;padding:7px 8px;border-top:1px solid var(--line);vertical-align:top}}th{{font-size:.72rem;text-transform:uppercase;color:var(--muted);letter-spacing:.04em}}tr.gap{{background:color-mix(in srgb,var(--bg) 60%,transparent)}}
.notam{{font-size:.76rem}}.notam.ficon{{border-color:var(--nogo)}}.notam.dim{{opacity:.7}}details summary{{cursor:pointer;font-weight:600;margin:4px 0}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.warn{{border-left:4px solid var(--marg);padding:10px 14px;background:var(--card);border-radius:8px}}
@media (max-width:820px){{.grid2,.cols{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<header class="top"><h1>Ryan Air · Morning Flight Briefing</h1><p class="muted">{esc(ts)} · Hubs PANI · PABE · PAOM · PAOT · PAUN · PAEM · PASM · PANC · PADQ</p>
<p class="warn small">Decision-support summary built from automated data. It is <b>not</b> an official weather briefing. Pilots and dispatch must still get a standard briefing (1-800-WX-BRIEF), check NOTAMs and FICONs, and follow the company's operations specifications.</p></header>

<h2>Flyability overview</h2>
<div class="hubs">{''.join(hub_cards)}</div>
<p class="small muted">The score starts at 100. Points come off for flight category now and forecast, wind, gusts and crosswind, freezing precipitation, thunderstorms, wind shear, icing risk, SIGMET/G-AIRMET areas, AAWU AIRMETs and PIREPs. 75 and up is GOOD, 50–74 MARGINAL, 25–49 POOR, below 25 NO-GO. The forecast window is the next 12 hours.</p>

<h2>Weather systems analysis</h2>
{f'<div class="card ai">{ai_html}</div>' if ai_html else '<p class="muted small">The Claude-written narrative wasn\'t generated. See the data below.</p>'}
<div class="cols" style="margin-top:16px">
  <div class="card"><h4>Surface pressure pattern (METAR)</h4>{pa_html or '<p class="muted">No data</p>'}
    <h4>AAWU synopsis</h4>{''.join(f'<p>{esc(s)}</p>' for s in fa_syn) or '<p class="muted">No synopsis available</p>'}</div>
  <div class="card"><h4>NWS forecast discussions</h4>{afd_html or '<p class="muted">No forecast discussions available</p>'}</div>
</div>
<h4>NEXRAD radar</h4><div class="imgs">{radar_html}</div>
<h4>Satellite</h4><div class="imgs">{sat_html}</div>

<h2>Hazards</h2>
<div class="cols">
  <div class="card"><h4>SIGMETs (Anchorage FIR)</h4>{sig_html}<h4>AIRMETs (AAWU)</h4>{wa_html or '<p class="muted">None</p>'}</div>
  <div class="card"><h4>PIREPs: moderate or greater icing/turbulence, last 12h</h4><ul>{pirep_html}</ul><h4>NWS alerts</h4><ul>{alert_html}</ul></div>
</div>

<h2>Hubs &amp; surrounding villages</h2>
{''.join(hub_details)}

<h2>Data sources</h2><ul class="small">{src_html}</ul>
<p class="small muted">Runway conditions are inferred from recent precipitation and temperature unless a FICON NOTAM is available. Villages with no METAR use Open-Meteo model guidance, plus the NWS point forecast and Weather Underground where configured. Generated by briefing.py.</p>
</div></body></html>"""


if __name__ == "__main__":
    sys.exit(main())
