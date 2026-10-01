"""Region configurations for briefing.py (select with --region)."""

ALASKA = {
    "key": "alaska",
    "title": "Ryan Air · Morning Flight Briefing",
    "page_title": "Ryan Air Morning Briefing",
    "tz": "America/Anchorage",
    "out": "out",
    "nearby_label": "Villages",
    "station_bbox": "56,-172.8,69.5,-147.5",
    "pirep_bbox": "55,-175,70,-145",
    "alerts_area": "AK",
    "alerts_label": "Alaska",
    # Alaska Aviation Weather Unit products (area forecasts, AIRMETs, SIGMETs).
    "aawu": True,
    "afd_offices": {"AFC": "Anchorage (AFC)", "AFG": "Fairbanks (AFG)"},
    "disclaimer": "Pilots and dispatch must still get a standard briefing (1-800-WX-BRIEF), check NOTAMs and FICONs, and follow the company's operations specifications.",
    "hubs": {
        "PANC": {"name": "Anchorage", "radius": 45, "fa": ["COOK INLET"]},
        "PANI": {"name": "Aniak", "radius": 90, "fa": ["KUSKOKWIM"]},
        "PABE": {"name": "Bethel", "radius": 100, "fa": ["KUSKOKWIM DELTA", "Y-K DELTA", "YK DELTA", "KUSKOKWIM"]},
        "PASM": {"name": "St. Mary's", "radius": 60, "fa": ["LWR YUKON", "Y-K DELTA", "YK DELTA", "KUSKOKWIM DELTA"]},
        "PAEM": {"name": "Emmonak", "radius": 60, "fa": ["LWR YUKON", "Y-K DELTA", "YK DELTA", "KUSKOKWIM DELTA"]},
        "PAUN": {"name": "Unalakleet", "radius": 90, "fa": ["NORTON SOUND"]},
        "PAOM": {"name": "Nome", "radius": 150, "fa": ["SEWARD PEN", "ST LAWRENCE"]},
        "PAOT": {"name": "Kotzebue", "radius": 150, "fa": ["KOBUK", "NOATAK", "KOTZEBUE"]},
        "PADQ": {"name": "Kodiak", "radius": 80, "fa": ["KODIAK"]},
        "PASA": {"name": "Savoonga (St. Lawrence Is.)", "radius": 40, "fa": ["ST LAWRENCE"]},
    },
    # How Bypass Mail is routed: hub -> bush points by airport code, from USPS
    # Handbook PO-508, Appendix A, Attachment D (March 2012 edition). Stations and
    # villages on these routes are grouped under their mail hub; anything else
    # (e.g. Kodiak villages, Diomede, non-village stations) falls back to the
    # nearest hub within its radius and is labelled "nearby".
    "mail_routes": {
        "PANI": ["ANV", "CHU", "CKD", "KGX", "HCR", "KLG", "RDV", "RSH", "SHX", "SLQ", "SRV"],
        "PABE": ["KKI", "AKI", "ATT", "CYF", "VAK", "EEK", "GNU", "HPB", "KUK", "KPN", "KKH", "KWT", "KWK",
                 "MLL", "MYU", "WNA", "PKA", "WWT", "NME", "NUP", "PTU", "KWN", "SCM", "OOK", "TLT", "WTL", "TNK"],
        "PAEM": ["AUK", "KOT", "SXP"],
        "PASM": ["MOU", "PQS"],
        "PAUN": ["KKA", "SMK", "SKK", "WBB"],
        "PAOM": ["KTS", "ELI", "GLV", "SHH", "TLA", "TNC", "WAA", "WMO"],
        "PASA": ["GAM"],
        "PAOT": ["ABL", "BKC", "DRG", "IAN", "KVL", "OBU", "WTK", "ORV", "PHO", "WLK", "SHG"],
    },
    # Bush points that PO-508 routes through mail hubs outside this briefing
    # (Galena, McGrath, Dillingham, King Salmon, Iliamna, Cold Bay, Port Heiden);
    # their stations are left out rather than grouped under the wrong hub.
    "other_mail_codes": ["GAL", "HUS", "HSL", "KAL", "KYU", "NUL", "RBY", "MCG", "NIB", "TCT", "TLJ",
                         "DLG", "WKK", "CLP", "KEK", "KGK", "KMO", "KNW", "TOG", "TWA", "AKN", "EGX",
                         "KLL", "PIP", "WSN", "ILI", "KNK", "NNL", "PDB", "PTA", "CDB", "KFP", "KVC",
                         "NLG", "PML", "PTH", "KCG", "KCL", "KCQ", "KPV"],
    # Stations whose IATA/FAA IDs in the feed don't carry the mail stop code.
    "station_mail_codes": {"PFKO": "KOT", "PFZK": "KKI", "POKA": "TNK", "PPIT": "NUP"},
    # Mail stop code for each village in "villages" (used when a village has no METAR).
    "village_mail_codes": {
        "Anvik": "ANV", "Chuathbaluk": "CHU", "Crooked Creek": "CKD", "Grayling": "KGX", "Holy Cross": "HCR",
        "Kalskag": "KLG", "Lower Kalskag": "KLG", "Red Devil": "RDV", "Russian Mission": "RSH", "Shageluk": "SHX",
        "Sleetmute": "SLQ", "Stony River": "SRV",
        "Akiachak": "KKI", "Akiak": "AKI", "Atmautluak": "ATT", "Chefornak": "CYF", "Chevak": "VAK", "Eek": "EEK",
        "Goodnews Bay": "GNU", "Hooper Bay": "HPB", "Kasigluk": "KUK", "Kipnuk": "KPN", "Kongiganak": "KKH",
        "Kwethluk": "KWT", "Kwigillingok": "KWK", "Marshall": "MLL", "Mekoryuk": "MYU", "Napakiak": "WNA",
        "Napaskiak": "PKA", "Newtok": "WWT", "Nightmute": "NME", "Nunapitchuk": "NUP", "Platinum": "PTU",
        "Quinhagak": "KWN", "Scammon Bay": "SCM", "Toksook Bay": "OOK", "Tuluksak": "TLT", "Tuntutuliak": "WTL",
        "Tununak": "TNK",
        "Alakanuk": "AUK", "Kotlik": "KOT", "Nunam Iqua": "SXP",
        "Mountain Village": "MOU", "Pilot Station": "PQS", "Pitkas Point": "PQS",
        "Koyuk": "KKA", "St. Michael": "SMK", "Shaktoolik": "SKK", "Stebbins": "WBB",
        "Brevig Mission": "KTS", "Elim": "ELI", "Golovin": "GLV", "Shishmaref": "SHH", "Teller": "TLA",
        "Wales": "WAA", "White Mountain": "WMO", "Gambell": "GAM",
        "Ambler": "ABL", "Buckland": "BKC", "Deering": "DRG", "Kiana": "IAN", "Kivalina": "KVL", "Kobuk": "OBU",
        "Noatak": "WTK", "Noorvik": "ORV", "Point Hope": "PHO", "Selawik": "WLK", "Shungnak": "SHG",
    },
    # Ryan Air villages and Kodiak-area villages (approximate). Used to fill gaps
    # where no METAR station exists near a served community.
    "villages": {
        "Anvik": (62.66, -160.19), "Chuathbaluk": (61.57, -159.25), "Crooked Creek": (61.87, -158.11),
        "Grayling": (62.9, -160.07), "Holy Cross": (62.2, -159.77), "Kalskag": (61.54, -160.31),
        "Red Devil": (61.76, -157.31), "Lower Kalskag": (61.51, -160.36), "Newtok": (60.94, -164.63),
        "Pitkas Point": (62.03, -163.29), "Russian Mission": (61.79, -161.32), "Shageluk": (62.68, -159.56),
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
    },
    "radars": [
        ("ALASKA", "Alaska mosaic"),
        ("PABC", "Bethel NEXRAD"),
        ("PAEC", "Nome NEXRAD"),
        ("PAKC", "King Salmon NEXRAD"),
        ("PAHG", "Kenai NEXRAD"),
    ],
    "sat": {
        "name": "GOES-18",
        "sector_label": "GOES-18 Alaska sector",
        "cdn": "https://cdn.star.nesdis.noaa.gov/GOES18/ABI/SECTOR/ak",
        "file": "GOES18-ABI-ak-{band}-1000x1000.jpg",
    },
    "sfc_analysis": {
        "source": "AAWU",
        "heading": "Surface analysis, last 24 h (AAWU)",
        "note": "6-hourly analyses from the Alaska Aviation Weather Unit. Watch how the lows and fronts have moved.",
        "urls": [f"https://tgftp.nws.noaa.gov/fax/PYCA0{i}.gif" for i in range(4)],
        "step_h": 6,
    },
    "sfc_forecast": {
        "source": "OPC",
        "heading": "Surface forecast, 0–96 h (OPC)",
        "note": "Ocean Prediction Center Arctic analysis and 24/48/72/96 h forecasts.",
        "frames": [("https://ocean.weather.gov/shtml/arctic/Arctic_00hrsfc.gif", "Analysis")] + [
            (f"https://ocean.weather.gov/shtml/arctic/{h}SFC_LATEST.gif", f"+{h} h forecast") for h in (24, 48, 72, 96)],
    },
}

DENVER = {
    "key": "denver",
    "title": "Denver Area · Flight Briefing",
    "page_title": "Denver Area Flight Briefing",
    "tz": "America/Denver",
    "out": "out-denver",
    "nearby_label": "Nearby airports",
    "station_bbox": "38.0,-107.6,41.2,-102.9",
    # Always brief these, even beyond every hub's radius (grouped under the nearest hub).
    "include_stations": ["KBJC", "KAPA", "KLIC", "KFMM", "KAKO", "KCFO", "KEIK", "KLMO", "KGXY", "KFLY"],
    "pirep_bbox": "37,-109.5,41.5,-102",
    "alerts_area": "CO",
    "alerts_label": "Colorado",
    "aawu": False,
    "afd_offices": {"BOU": "Denver/Boulder (BOU)", "PUB": "Pueblo (PUB)", "GJT": "Grand Junction (GJT)"},
    "cwsu": "ZDV",  # Denver Center Weather Service Unit: Center Weather Advisories
    "pressure_max_elev_m": 2000,  # skip mountain stations (bad sea-level reductions) in the pressure analysis
    "disclaimer": "Pilots must still get a standard briefing (1-800-WX-BRIEF or 1800wxbrief.com), check NOTAMs and TFRs, and stay within their personal and operational minimums.",
    "hubs": {
        "KDEN": {"name": "Denver Intl", "radius": 20},
        "KAPA": {"name": "Centennial", "radius": 14},
        "KBJC": {"name": "Rocky Mountain Metro", "radius": 14},
        "KFNL": {"name": "Northern Colorado (Fort Collins/Loveland)", "radius": 30},
        "KCOS": {"name": "Colorado Springs", "radius": 40},
        "KEGE": {"name": "Eagle County (mountains)", "radius": 45},
    },
    "villages": {},
    "radars": [
        ("KFTG", "Denver/Front Range NEXRAD"),
        ("KPUX", "Pueblo NEXRAD"),
        ("KCYS", "Cheyenne NEXRAD"),
        ("KGJX", "Grand Junction NEXRAD"),
        ("SOUTHROCKIES", "Southern Rockies mosaic"),
    ],
    "sat": {
        "name": "GOES-19",
        "sector_label": "GOES-19 (GOES-East) Southern Rockies sector",
        "cdn": "https://cdn.star.nesdis.noaa.gov/GOES19/ABI/SECTOR/sr",
        "file": "GOES19-ABI-sr-{band}-1200x1200.jpg",
    },
    "sfc_analysis": {
        "source": "WPC",
        "heading": "Surface analysis, last 24 h (WPC)",
        "note": "3-hourly North America analyses from the Weather Prediction Center. Watch the fronts and the Front Range pressure pattern.",
        "urls": [f"https://www.wpc.ncep.noaa.gov/sfc/namussfc{h:02d}wbg.gif" for h in range(0, 24, 3)],
        "step_h": 3,
    },
    "sfc_forecast": {
        "source": "WPC",
        "heading": "Surface forecast, next 2 days (WPC)",
        "note": "Weather Prediction Center fronts, pressure and weather-type forecasts at 12-hour steps. Each chart's valid time is printed at the bottom.",
        "frames": [("https://www.wpc.ncep.noaa.gov/sfc/namussfcwbg.gif", "Latest analysis")] + [
            (f"https://www.wpc.ncep.noaa.gov/basicwx/{code}fwbg.gif", f"Forecast {n} of 4 (valid time on chart)")
            for n, code in enumerate(("92", "94", "96", "98"), 1)],
    },
}

REGIONS = {r["key"]: r for r in (ALASKA, DENVER)}
