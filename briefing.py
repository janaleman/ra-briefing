#!/usr/bin/env python3
"""Flight briefing generator (Ryan Air Alaska network, Denver area, ...).

Collects weather, hazards, NOTAMs and runway information for a region's hubs
and their surrounding airports/villages, scores flyability, and writes a
self-contained HTML page to the region's output folder (with a dated copy in
archive/). Regions are defined in regions.py and chosen with --region.

Sources: aviationweather.gov (METAR, TAF, PIREP, SIGMET, airport data),
api.weather.gov (AAWU area forecasts/AIRMETs/SIGMETs, forecast discussions,
alerts, point forecasts), NEXRAD RIDGE radar imagery, Open-Meteo (model
forecast for villages with no weather station), optional Weather Underground
PWS (WU_API_KEY) and optional FAA NOTAM API (FAA_CLIENT_ID/FAA_CLIENT_SECRET).

Stdlib only.
Usage: python3 briefing.py [--region alaska|denver] [--open]
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import html
import json
import math
import os
import re
import subprocess
import sys
import urllib.parse
import urllib.request
from pathlib import Path
from zoneinfo import ZoneInfo

from regions import REGIONS

HERE = Path(__file__).resolve().parent
OUT = HERE / "out"
UA = "FlightBriefing/1.0 (ops briefing tool)"
UA_SUFFIX = "(ops briefing tool)"
SAT_BANDS = [
    ("GEOCOLOR", "GeoColor (true color by day, IR clouds and city lights at night)"),
    ("13", "Clean longwave IR (band 13): cloud-top temperature, day and night"),
    ("AirMass", "Air Mass RGB: jet streaks, dry intrusions and frontal boundaries"),
]
SAT_HOURS, SAT_STEP_MIN = 6, 30

# Region-specific settings; configure() fills these from regions.py.
CFG = {}
HUBS, VILLAGES, RADARS = {}, {}, []
TZ = ZoneInfo("America/Anchorage")


def configure(name):
    global CFG, HUBS, VILLAGES, RADARS, TZ, OUT
    CFG = REGIONS[name]
    HUBS, VILLAGES, RADARS = CFG["hubs"], CFG["villages"], CFG["radars"]
    TZ = ZoneInfo(CFG["tz"])
    OUT = HERE / CFG["out"]


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
    return t.astimezone(TZ).strftime("%a %H:%M %Z")


def chunks(seq, n):
    for i in range(0, len(seq), n):
        yield seq[i:i + n]


# ---------------------------------------------------------------- collection

def mail_hub_by_code():
    """Mail stop code -> hub ICAO, from the region's mail routes (empty if none)."""
    return {code: hub for hub, codes in CFG.get("mail_routes", {}).items() for code in codes}


def collect_stations():
    """METAR stations grouped under hubs.

    A station on a mail route goes under its mail hub ("mail"); any other station
    goes under the nearest hub within that hub's radius ("nearby").
    """
    info = awc("stationinfo", bbox=CFG["station_bbox"]) or []
    hub_pos = {x["icaoId"]: (x["lat"], x["lon"]) for x in info if x["icaoId"] in HUBS}
    by_code, overrides = mail_hub_by_code(), CFG.get("station_mail_codes", {})
    stations = {}
    for s in info:
        if "METAR" not in (s.get("siteType") or []):
            continue
        icao, pos = s["icaoId"], (s["lat"], s["lon"])
        codes = {s.get("iataId"), s.get("faaId"), overrides.get(icao)} - {None}
        mail_hub = next((by_code[c] for c in codes if c in by_code), None)
        if icao not in HUBS and codes & set(CFG.get("other_mail_codes", [])):
            continue  # mail goes through a hub outside this briefing
        if icao in HUBS:
            hub, route = icao, "hub"
        elif mail_hub and mail_hub in hub_pos:
            hub, route = mail_hub, "mail"
        else:
            near = [(h, nm(pos, p)) for h, p in hub_pos.items() if nm(pos, p) <= HUBS[h]["radius"]]
            if not near and icao in CFG.get("include_stations", []):
                near = [(h, nm(pos, p)) for h, p in hub_pos.items()]  # required station: nearest hub at any range
            if not near:
                continue
            hub, route = min(near, key=lambda x: x[1])[0], "nearby"
        stations[icao] = {
            "id": icao, "name": (s.get("site") or icao).replace(" Arpt", ""),
            "lat": s["lat"], "lon": s["lon"], "elev": s.get("elev"),
            "hub": hub, "route": route,
            "dist": 0 if route == "hub" else round(nm(pos, hub_pos[hub])),
            "has_taf": "TAF" in (s.get("siteType") or []),
            "codes": sorted(codes),
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
    for office in CFG["afd_offices"]:
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
    d = get(f"https://api.weather.gov/alerts/active?area={CFG['alerts_area']}", name="api.weather.gov/alerts") or {}
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


def collect_satellite():
    """Download the last SAT_HOURS of GOES frames per band for the region's sector.

    Returns band -> list of {"src", "label"} (oldest first); files land in out/img/sat/<band>/.
    """
    sat = CFG["sat"]
    cutoff = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=SAT_HOURS, minutes=5)
    jobs = {}
    for band, _ in SAT_BANDS:
        listing = get(f"{sat['cdn']}/{band}/", json_=False, name=f"{sat['name']} satellite (NESDIS)")
        if not listing:
            continue
        names = sorted(set(re.findall(r"(\d{11})_" + re.escape(sat["file"].format(band=band)), listing.decode(errors="replace"))))
        # Walk back from the newest image, keeping one about every SAT_STEP_MIN minutes
        # (sectors scan at different minutes past the hour).
        frames, last = [], None
        for stamp in reversed(names):
            t = dt.datetime.strptime(stamp, "%Y%j%H%M").replace(tzinfo=dt.timezone.utc)
            if t < cutoff:
                break
            if last is None or (last - t) >= dt.timedelta(minutes=SAT_STEP_MIN - 2):
                frames.append((stamp, t))
                last = t
        jobs[band] = frames[::-1]

    out = {}
    def fetch(band, stamp, t):
        dest = OUT / "img" / "sat" / band / f"{stamp}.jpg"
        dest.parent.mkdir(parents=True, exist_ok=True)
        b = get(f"{sat['cdn']}/{band}/{stamp}_{sat['file'].format(band=band)}", json_=False, name=f"{sat['name']} satellite (NESDIS)")
        if not b:
            return None
        dest.write_bytes(b)
        return {"src": f"img/sat/{band}/{stamp}.jpg", "label": local(t.timestamp()) + t.strftime(" · %H%MZ"), "path": str(dest)}
    with cf.ThreadPoolExecutor(8) as ex:
        for band, frames in jobs.items():
            res = list(ex.map(lambda f: fetch(band, *f), frames))
            out[band] = [r for r in res if r]
    return out


def fetch_with_time(url, dest, name):
    """Download url to dest; return its Last-Modified time (UTC) or None on failure."""
    from email.utils import parsedate_to_datetime
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    err = None
    for _ in range(3):  # tgftp.nws.noaa.gov times out now and then
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                body, lm = r.read(), r.headers.get("Last-Modified")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(body)
            SOURCES.setdefault(name, "ok")
            return parsedate_to_datetime(lm) if lm else dt.datetime.now(dt.timezone.utc)
        except Exception as e:  # noqa: BLE001
            err = e
    SOURCES[name] = f"failed: {type(err).__name__}: {err}"[:200]
    return None


def collect_surface():
    """Recent surface analyses (oldest first) and the surface forecast sequence for the region."""
    an_cfg, fc_cfg = CFG["sfc_analysis"], CFG["sfc_forecast"]
    analyses = []
    for i, url in enumerate(an_cfg["urls"]):
        dest = OUT / "img" / "sfc" / f"an-{i}.gif"
        t = fetch_with_time(url, dest, f"{an_cfg['source']} surface analysis")
        if t:
            step = an_cfg["step_h"]
            valid = t.replace(hour=t.hour - t.hour % step, minute=0, second=0, microsecond=0)  # nominal synoptic time
            analyses.append({"src": f"img/sfc/an-{i}.gif", "label": valid.strftime("Analysis %HZ %a %b %-d"), "path": str(dest), "t": valid})
    analyses.sort(key=lambda a: a["t"])
    forecast = []
    for i, (url, label) in enumerate(fc_cfg["frames"]):
        dest = OUT / "img" / "sfc" / f"fc-{i}.gif"
        if fetch_with_time(url, dest, f"{fc_cfg['source']} surface forecast"):
            forecast.append({"src": f"img/sfc/fc-{i}.gif", "label": label, "path": str(dest)})
    return analyses, forecast


def collect_cwas():
    """Center Weather Advisories from the region's CWSU (e.g. ZDV for Denver Center)."""
    cwsu = CFG.get("cwsu")
    if not cwsu:
        return []
    return [c for c in (awc("cwa") or []) if c.get("cwsu") == cwsu]


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


# Penalty per G-AIRMET hazard; 0 = informational only (freezing level, high-altitude turbulence).
G_AIRMET_WEIGHTS = {"IFR": 10, "ICE": 10, "TURB-LO": 8, "SFC_WND": 8, "LLWS": 6, "MT_OBSC": 3,
                    "TURB-HI": 0, "FZLVL": 0, "M_FZLVL": 0}


def hazards_for(lat, lon, fa_zone_hits, gairmets, sigmets_geo):
    out = []
    for s in sigmets_geo:
        if s.get("coords") and point_in_poly(lat, lon, s["coords"]):
            out.append({"label": f"Inside SIGMET: {s.get('hazard')}", "penalty": 30})
    # G-AIRMETs repeat per forecast hour: count each hazard once, skip high-altitude
    # turbulence, and cap the combined penalty.
    seen_g, g_total = set(), 0
    for g in gairmets:
        key = (g.get("hazard"), g.get("severity"))
        gw = G_AIRMET_WEIGHTS.get(g.get("hazard"), 5)
        if key in seen_g or not gw or not g.get("coords"):
            continue
        if point_in_poly(lat, lon, g["coords"]):
            seen_g.add(key)
            w = min(gw, max(0, 20 - g_total))
            g_total += w
            out.append({"label": f"G-AIRMET {g.get('hazard')} {g.get('severity') or ''}".strip(), "penalty": w})
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
    # Sea-level pressure reduced from high mountain stations is unreliable, so
    # regions with high terrain set a maximum station elevation (metres).
    max_elev = CFG.get("pressure_max_elev_m")
    rows = []
    for icao, m in latest.items():
        if max_elev is not None and (m.get("elev") or 0) > max_elev:
            continue
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


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--region", default="alaska", choices=sorted(REGIONS), help="which region to brief (see regions.py)")
    ap.add_argument("--open", action="store_true", help="open the briefing in the browser when done")
    args = ap.parse_args()
    configure(args.region)

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
        f_pirep = ex.submit(awc, "pirep", bbox=CFG["pirep_bbox"], age=12)
        f_gair = ex.submit(awc, "gairmet")
        f_isig = ex.submit(awc, "isigmet") if CFG["aawu"] else ex.submit(awc, "airsigmet")
        f_aawu = ex.submit(collect_aawu) if CFG["aawu"] else ex.submit(lambda: ([], [], []))
        f_cwa = ex.submit(collect_cwas)
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
        f_sat = ex.submit(collect_satellite)
        f_sfc = ex.submit(collect_surface)

    metars, tafs, airports = f_metar.result(), f_taf.result(), f_apt.result()
    pireps = f_pirep.result() or []
    gairmets = [g for g in (f_gair.result() or []) if g.get("coords")]
    hub_pts = [(stations[h]["lat"], stations[h]["lon"]) for h in HUBS if h in stations]
    if CFG["aawu"]:
        isig = [s for s in (f_isig.result() or []) if str(s.get("firId", "")).startswith("PA")]
    else:
        # Domestic SIGMETs (convective and non-convective) touching the region.
        isig = [s for s in (f_isig.result() or []) if s.get("coords") and any(
            point_in_poly(lat, lon, s["coords"]) for lat, lon in hub_pts)]
    cwas = [c for c in f_cwa.result() if c.get("coords")]
    isig += [{**c, "hazard": f"CWA {c.get('hazard')}", "rawSigmet": c.get("cwaText") or c.get("rawCwa") or json.dumps(c)[:600]} for c in cwas]
    fa, wa, aawu_sig = f_aawu.result()
    afd = f_afd.result()
    alerts = f_alert.result()
    notams, notam_status = f_notam.result()
    radar_files = [f.result() for f in f_radar if f.result()]
    sat = f_sat.result()
    sfc_an, sfc_fc = f_sfc.result()

    # Villages with no METAR station within 8 nm get a model forecast.
    # Their hub follows the mail route when known, otherwise the nearest hub.
    # A village counts as observed only by a station at its own airport (same mail
    # stop code, within 8 nm); villages without a code need a station within 3 nm.
    gap_villages, by_code = [], mail_hub_by_code()
    vcodes = CFG.get("village_mail_codes", {})
    def observed(v, pos):
        code = vcodes.get(v)
        if code:
            return any(code in st.get("codes", []) and nm(pos, (st["lat"], st["lon"])) <= 8 for st in stations.values())
        return any(nm(pos, (st["lat"], st["lon"])) <= 3 for st in stations.values())
    for v, pos in VILLAGES.items():
        if not observed(v, pos):
            mail_hub = by_code.get(CFG.get("village_mail_codes", {}).get(v))
            if mail_hub in stations:
                hub, route = mail_hub, "mail"
            else:
                hub = min(HUBS, key=lambda h: nm(pos, (stations[h]["lat"], stations[h]["lon"])) if h in stations else 1e9)
                route = "nearby"
            gap_villages.append({"name": v, "lat": pos[0], "lon": pos[1], "hub": hub, "route": route,
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
        hit_zones = [z for z in zones if any(k in z for k in hub.get("fa", []))]
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
                afd_ex.append({"office": CFG["afd_offices"][off], "title": k, "text": v, "time": d["time"]})


    page = render(now, results, gap_villages, zones, fa_synopsis, wa, aawu_sig, isig, gairmets, sig_pireps,
                  alerts, afd_ex, pa, precip_now, notam_status, sat, sfc_an, sfc_fc, stations)
    path = OUT / "briefing.html"
    path.write_text(page)
    stamp = now.astimezone(TZ).strftime("%Y-%m-%d")
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


def route_tag(route):
    if not CFG.get("mail_routes") or route == "hub":
        return ""
    return ' <span class="tag mail">mail route</span>' if route == "mail" else ' <span class="tag near">nearby</span>'


def fmt_cig(c):
    return "—" if c is None else f"{int(c):,}"


def loop_player(frames, title, note="", lazy=True, start_last=True):
    """Animated frame player. Frames load on first view (or tab open) to keep the page light."""
    if not frames:
        return f'<div class="loop empty"><p class="muted small">{esc(title)}: no images available this run.</p></div>'
    data = esc(json.dumps([{"src": f["src"], "label": f["label"]} for f in frames]))
    first = frames[-1 if start_last else 0]
    return f"""<div class="loop" data-frames="{data}" data-lazy="{'1' if lazy else '0'}">
      <div class="loop-stage"><img alt="{esc(title)}" src="{'' if lazy else esc(first['src'])}" data-first="{esc(first['src'])}"><span class="loop-time mono">{esc(first['label'])}</span></div>
      <div class="loop-ctrl">
        <button type="button" class="lp-play" aria-label="Play">▶</button>
        <input type="range" class="lp-pos" min="0" max="{len(frames) - 1}" value="{len(frames) - 1 if start_last else 0}" aria-label="Frame">
        <select class="lp-speed" aria-label="Speed"><option value="900">Slow</option><option value="450" selected>Normal</option><option value="200">Fast</option></select>
        <a class="small" href="{esc(first['src'])}" target="_blank">Full size ↗</a>
      </div>
      {f'<p class="small muted">{esc(note)}</p>' if note else ''}
    </div>"""


def render(now, R, gaps, zones, fa_syn, wa, aawu_sig, isig, gairmets, pireps, alerts, afd_ex, pa, precip_now, notam_status, sat, sfc_an, sfc_fc, stations):
    ts = now.astimezone(TZ).strftime("%A %B %-d, %Y · %H:%M %Z")
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
          <div class="small muted">{esc(CFG['nearby_label'])}: {dist or '—'}</div>
          <div class="small rw rw-{r['runway']['level']}">Runway: {esc(r['runway']['text'].split(' (')[0])}</div>
        </a>""")

        rows = []
        for x in sorted(vill, key=lambda x: (x["route"] != "mail", x["dist"])):
            rows.append(f"""<tr>
              <td><b>{esc(x['name'])}</b><br><span class="mono muted">{x['id']} · {x['dist']} nm</span>{route_tag(x['route'])}</td>
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
              <td><b>{esc(g['name'])}</b><br><span class="mono muted">no METAR · model</span>{route_tag(g['route'])}</td>
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
    sat_tabs = "".join(f'<button type="button" role="tab" data-tab="sat-{esc(b)}" aria-selected="{"true" if i == 0 else "false"}">{esc(lbl.split(" (")[0].split(":")[0])}</button>' for i, (b, lbl) in enumerate(SAT_BANDS))
    sat_panes = "".join(f'<div class="tabpane" id="sat-{esc(b)}" {"" if i == 0 else "hidden"}>{loop_player(sat.get(b, []), lbl, f"{lbl}. {CFG['sat']['sector_label']}, last {SAT_HOURS} h every {SAT_STEP_MIN} min.")}</div>' for i, (b, lbl) in enumerate(SAT_BANDS))
    sat_html = f'<div class="satwrap"><div class="tabs" role="tablist">{sat_tabs}</div>{sat_panes}</div>'
    sfc_html = f"""<div class="imgs2">
      <div><h4>{esc(CFG['sfc_analysis']['heading'])}</h4>{loop_player(sfc_an, CFG['sfc_analysis']['heading'], CFG['sfc_analysis']['note'], start_last=False)}</div>
      <div><h4>{esc(CFG['sfc_forecast']['heading'])}</h4>{loop_player(sfc_fc, CFG['sfc_forecast']['heading'], CFG['sfc_forecast']['note'], start_last=False)}</div>
    </div>"""
    pa_html = ""
    if pa:
        def nm_(i):
            return R[i]["name"] if i in R else i
        pa_html = f"""<ul>
          <li>Lowest pressure: <b>{esc(nm_(pa['low'][0]))}</b> {pa['low'][1]:.1f} hPa · highest: <b>{esc(nm_(pa['high'][0]))}</b> {pa['high'][1]:.1f} hPa. The {pa['gradient']} hPa spread across the region {'points to a tight gradient and strong winds' if pa['gradient'] >= 20 else 'is moderate' if pa['gradient'] >= 10 else 'is weak, so winds should be light'}.</li>
          {f"<li>Fastest falling: <b>{esc(nm_(pa['fall'][0]))}</b> {pa['fall'][2]:+.1f} hPa/3h{', a system approaching' if pa['fall'][2] <= -2 else ''}.</li>" if pa['fall'] and pa['fall'][2] < 0 else ''}
          {f"<li>Fastest rising: <b>{esc(nm_(pa['rise'][0]))}</b> {pa['rise'][2]:+.1f} hPa/3h{', clearing or building behind a system' if pa['rise'][2] >= 2 else ''}.</li>" if pa['rise'] and pa['rise'][2] > 0 else ''}
          {"<li>Pressure is rising or steady everywhere; no station reports a 3-hour fall.</li>" if pa['fall'] and pa['fall'][2] >= 0 else ''}
          <li>Stations reporting precipitation: {esc(', '.join(precip_now) or 'none')}</li>
        </ul>"""
    afd_html = "".join(f"<details {'open' if i < 2 else ''}><summary>{esc(a['office'])}: {esc(a['title'])} <span class='muted small'>{esc(local(a['time']))}</span></summary><pre>{esc(a['text'])}</pre></details>" for i, a in enumerate(afd_ex))
    sig_html = "".join(f"<pre class='notam ficon'>{esc(p['text'].strip()[:1200])}</pre>" for p in aawu_sig) \
        + "".join(f"<pre class='notam ficon'>{esc(s.get('rawSigmet') or s.get('hazard'))}</pre>" for s in isig) or f"<p class='muted'>No active SIGMETs or CWAs for the {esc(CFG['alerts_label'])} briefing area.</p>"
    wa_html = "".join(f"<details><summary>AIRMET bulletin {esc(local(p['time']))}</summary><pre>{esc(p['text'])}</pre></details>" for p in wa)
    if not CFG["aawu"]:
        # Outside Alaska, AIRMETs come as G-AIRMET polygons: list those over the hubs, current or upcoming.
        rows = {}
        for g in gairmets:
            over = [HUBS[h]["name"] for h in HUBS if h in stations and point_in_poly(stations[h]["lat"], stations[h]["lon"], g["coords"])]
            if over:
                key = (g.get("hazard"), g.get("severity"), g.get("due_to"))
                r = rows.setdefault(key, {"hubs": set(), "hours": set(), "base": g.get("base"), "top": g.get("top")})
                r["hubs"].update(over)
                r["hours"].add(g.get("forecastHour"))
        wa_html = "".join(f"<li><b>{esc(k[0])}</b> {esc(k[1] or '')} {esc(k[2] or '')} <span class='muted small'>{esc(r['base'] or '')}–{esc(r['top'] or '')} · +{', +'.join(str(h) for h in sorted(x for x in r['hours'] if x is not None))} h · over {esc(', '.join(sorted(r['hubs'])))}</span></li>" for k, r in sorted(rows.items(), key=lambda kv: str(kv[0])))
        wa_html = f"<ul>{wa_html}</ul>" if wa_html else ""
    pirep_html = "".join(f"<li class='mono small'>{esc(p['rawOb'])}</li>" for p in pireps[:25]) or "<li class='muted'>No moderate or greater icing or turbulence reports in the last 12 hours.</li>"
    relevant_alerts = [a for a in alerts if not re.search(r"Test", a.get("event", ""))]
    alert_html = "".join(f"<li><b>{esc(a['event'])}</b>: {esc(a['areaDesc'][:220])} <span class='muted small'>until {esc(local(a.get('ends') or a.get('expires')))}</span></li>" for a in relevant_alerts) or f"<li class='muted'>No active NWS alerts for {esc(CFG['alerts_label'])}.</li>"
    src_html = "".join(f"<li><span class='mono'>{esc(k)}</span>: {'✅' if v == 'ok' else '⚠️ ' + esc(v)}</li>" for k, v in sorted(SOURCES.items()))

    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(CFG['page_title'])}</title>
<meta name="generated" content="{now.isoformat(timespec='seconds')}">
<meta http-equiv="refresh" content="300">
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
.tag{{display:inline-block;font-size:.66rem;font-weight:600;padding:0 6px;border-radius:4px;margin-left:4px;vertical-align:1px}}.tag.mail{{background:color-mix(in srgb,var(--accent) 20%,transparent);color:var(--accent)}}.tag.near{{border:1px solid var(--line);color:var(--muted)}}
.rw-ok{{color:var(--good)}}.rw-warn{{color:var(--marg)}}.rw-bad,.rw-reported{{color:var(--nogo)}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px}}
.imgs{{display:grid;grid-template-columns:repeat(auto-fill,minmax(300px,1fr));gap:12px}}.imgs2{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}
.loop{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:10px}}.loop-stage{{position:relative;background:#000;border-radius:6px;overflow:hidden;min-height:200px}}.loop-stage img{{width:100%;display:block}}
.loop-time{{position:absolute;left:8px;top:8px;background:rgba(0,0,0,.7);color:#fff;font-size:.78rem;padding:2px 8px;border-radius:5px}}
.loop-ctrl{{display:flex;gap:10px;align-items:center;margin-top:8px}}.loop-ctrl input[type=range]{{flex:1}}.loop-ctrl button,.loop-ctrl select,.tabs button{{font:inherit;font-size:.85rem;border:1px solid var(--line);background:var(--bg);color:var(--ink);border-radius:6px;padding:4px 10px;cursor:pointer}}
.satwrap{{max-width:820px}}.tabs{{display:flex;gap:6px;margin-bottom:8px;flex-wrap:wrap}}.tabs button[aria-selected=true]{{background:var(--accent);border-color:var(--accent);color:#fff}}figure{{margin:0;background:var(--card);border:1px solid var(--line);border-radius:10px;overflow:hidden}}figure img{{width:100%;display:block}}figcaption{{padding:6px 10px;font-size:.82rem}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:20px}}.hubd{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:16px 18px;margin-bottom:16px}}
.hubd>header{{display:flex;gap:10px;align-items:center;flex-wrap:wrap}}.score-sm{{font-weight:700}}
.tablewrap{{overflow-x:auto;margin-top:14px}}table{{width:100%;border-collapse:collapse;font-size:.85rem}}th,td{{text-align:left;padding:7px 8px;border-top:1px solid var(--line);vertical-align:top}}th{{font-size:.72rem;text-transform:uppercase;color:var(--muted);letter-spacing:.04em}}tr.gap{{background:color-mix(in srgb,var(--bg) 60%,transparent)}}
.notam{{font-size:.76rem}}.notam.ficon{{border-color:var(--nogo)}}.notam.dim{{opacity:.7}}details summary{{cursor:pointer;font-weight:600;margin:4px 0}}
.cols{{display:grid;grid-template-columns:1fr 1fr;gap:16px}}.warn{{border-left:4px solid var(--marg);padding:10px 14px;background:var(--card);border-radius:8px}}
@media (max-width:820px){{.grid2,.cols,.imgs2{{grid-template-columns:1fr}}}}
</style></head><body><div class="wrap">
<header class="top"><h1>{esc(CFG['title'])}</h1><p class="muted">{esc(ts)} · Hubs {' · '.join(HUBS)}</p>
<p class="warn small">Decision-support summary built from automated data. It is <b>not</b> an official weather briefing. {esc(CFG['disclaimer'])}</p></header>

<h2>Flyability overview</h2>
<div class="hubs">{''.join(hub_cards)}</div>
<p class="small muted">The score starts at 100. Points come off for flight category now and forecast, wind, gusts and crosswind, freezing precipitation, thunderstorms, wind shear, icing risk, SIGMET/G-AIRMET{"" if CFG["aawu"] else "/CWA"} areas{", AAWU AIRMETs" if CFG["aawu"] else ""} and PIREPs. 75 and up is GOOD, 50–74 MARGINAL, 25–49 POOR, below 25 NO-GO. The forecast window is the next 12 hours.</p>

<h2>Weather systems analysis</h2>
<div class="cols">
  <div class="card"><h4>Surface pressure pattern (METAR)</h4>{pa_html or '<p class="muted">No data</p>'}
    {(f"<h4>AAWU synopsis</h4>" + (''.join(f'<p>{esc(s)}</p>' for s in fa_syn) or '<p class="muted">No synopsis available</p>')) if CFG["aawu"] else ""}</div>
  <div class="card"><h4>NWS forecast discussions</h4>{afd_html or '<p class="muted">No forecast discussions available</p>'}</div>
</div>
<h3 style="margin-top:24px">Surface analysis</h3>
{sfc_html}
<h3 style="margin-top:24px">Satellite ({esc(CFG['sat']['name'])})</h3>
{sat_html}
<h3 style="margin-top:24px">NEXRAD radar</h3><div class="imgs">{radar_html}</div>

<h2>Hazards</h2>
<div class="cols">
  <div class="card"><h4>{"SIGMETs (Anchorage FIR)" if CFG["aawu"] else "SIGMETs and Center Weather Advisories"}</h4>{sig_html}<h4>{"AIRMETs (AAWU)" if CFG["aawu"] else "G-AIRMETs over the hubs"}</h4>{wa_html or '<p class="muted">None</p>'}</div>
  <div class="card"><h4>PIREPs: moderate or greater icing/turbulence, last 12h</h4><ul>{pirep_html}</ul><h4>NWS alerts</h4><ul>{alert_html}</ul></div>
</div>

<h2>Hubs &amp; surrounding villages</h2>
{''.join(hub_details)}

<h2>Data sources</h2><ul class="small">{src_html}</ul>
<p class="small muted">Runway conditions are inferred from recent precipitation and temperature unless a FICON NOTAM is available. Villages with no METAR use Open-Meteo model guidance, plus the NWS point forecast and Weather Underground where configured. Generated by briefing.py.</p>
</div>
<script>
(() => {{
  function setup(el) {{
    if (el.dataset.ready) return;
    el.dataset.ready = "1";
    const frames = JSON.parse(el.dataset.frames), img = el.querySelector("img"), time = el.querySelector(".loop-time");
    const pos = el.querySelector(".lp-pos"), play = el.querySelector(".lp-play"), speed = el.querySelector(".lp-speed"), full = el.querySelector("a");
    frames.forEach(f => {{ const p = new Image(); p.src = f.src; }});  // preload
    let i = +pos.value, timer = null;
    const show = n => {{ i = (n + frames.length) % frames.length; img.src = frames[i].src; time.textContent = frames[i].label; pos.value = i; full.href = frames[i].src; }};
    const stop = () => {{ clearInterval(timer); timer = null; play.textContent = "▶"; play.setAttribute("aria-label", "Play"); }};
    const start = () => {{ stop(); timer = setInterval(() => show(i + 1), +speed.value); play.textContent = "❚❚"; play.setAttribute("aria-label", "Pause"); }};
    play.onclick = () => timer ? stop() : start();
    pos.oninput = () => {{ stop(); show(+pos.value); }};
    speed.onchange = () => {{ if (timer) start(); }};
    show(i);
    start();
  }}
  const visible = el => !el.closest("[hidden]");
  const io = new IntersectionObserver(es => es.forEach(e => {{ if (e.isIntersecting && visible(e.target)) {{ setup(e.target); io.unobserve(e.target); }} }}), {{ rootMargin: "200px" }});
  document.querySelectorAll(".loop[data-frames]").forEach(el => io.observe(el));
  document.querySelectorAll(".tabs").forEach(tabs => tabs.addEventListener("click", e => {{
    const b = e.target.closest("button[data-tab]"); if (!b) return;
    tabs.querySelectorAll("button").forEach(x => {{
      const on = x === b; x.setAttribute("aria-selected", on);
      const pane = document.getElementById(x.dataset.tab); pane.hidden = !on;
      if (on) pane.querySelectorAll(".loop[data-frames]").forEach(setup);
    }});
  }}));
}})();
</script>
</body></html>"""


if __name__ == "__main__":
    sys.exit(main())
