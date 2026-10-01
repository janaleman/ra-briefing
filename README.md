# Ryan Air morning briefing

`briefing.py` builds `out/briefing.html` every morning at 9:00 (systemd user timer `ra-briefing.timer`) and opens it in the browser. A dated copy is saved in `out/archive/`.

It covers hubs PANI, PABE, PAOM, PAOT, PAUN, PAEM, PASM, PANC and PADQ, every METAR station within each hub's radius, and every Ryan Air village. Villages without a station get model guidance.

```sh
python3 briefing.py            # full run, including the Claude-written narrative (about 1–2 min)
python3 briefing.py --no-ai    # data and scores only (about 20 s)
python3 briefing.py --open     # open in the browser when done

systemctl --user start ra-briefing.service   # run the scheduled job now
systemctl --user list-timers ra-briefing.timer
journalctl --user -u ra-briefing.service     # logs
```

## Optional keys: `~/.config/ra-briefing.env`

| Variable | Enables |
|---|---|
| `FAA_CLIENT_ID`, `FAA_CLIENT_SECRET` | NOTAMs and FICON runway condition reports. Free key from https://api.faa.gov/s/ |
| `WU_API_KEY` | Weather Underground personal-weather-station observations for villages with no METAR |

Without the FAA key, runway condition is **inferred** from recent precipitation and temperature. The page labels it that way.

## Sources

- **aviationweather.gov:** METAR, TAF, PIREP, international SIGMET, G-AIRMET, station and runway data.
- **api.weather.gov:** AAWU area forecasts (FA8/FA9), AIRMETs (WA8/WA9), SIGMETs (SIG/WSV), forecast discussions (AFC/AFG), alerts and point forecasts.
- **radar.weather.gov:** NEXRAD RIDGE for PABC, PAEC, PAKC, PAHG and the Alaska mosaic.
- **Open-Meteo:** hourly model forecast.
- **GOES-West:** satellite imagery. It's hidden if it doesn't load.

## Scoring

The score starts at 100. Points come off for:

- current and forecast flight category
- wind, gusts and crosswind on the best runway
- freezing precipitation, thunderstorms, low-level wind shear and icing risk
- SIGMET and G-AIRMET polygons
- AAWU AIRMETs (turbulence above FL250 is ignored; the combined deduction is capped at 20)
- PIREPs within 50 nm

GOOD is 75 and up, MARGINAL 50–74, POOR 25–49, NO-GO below 25. Thresholds are in `score_station()`.

This is decision support only, not an official briefing.

## Hosted deployment (GitHub Actions → Cloudflare)

The briefing rebuilds every 5 minutes. The Cloudflare Worker (`src/index.js`, cron in `wrangler.jsonc`) starts the GitHub Actions workflow on time; this needs a fine-grained GitHub token, with Actions read/write on this repo only, stored as the Worker secret `GH_DISPATCH_TOKEN`. The workflow's own `*/15` schedule is a fallback. Runs skip themselves if the live briefing is under 3 minutes old. Automatic refreshes make no Claude API call; they carry over the last analysis. To write a new one, run the workflow by hand with **Write AI analysis** ticked. The repo is public so Actions minutes are free.

In CI the narrative comes from the Claude API (`claude-opus-5`, with server-side refusal fallback) instead of the local `claude` CLI. The radar images are sent inline.

| Repo secret | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | for narrative | From console.anthropic.com |
| `CLOUDFLARE_API_TOKEN` | yes | Token from the **Edit Cloudflare Workers** template |
| `CLOUDFLARE_ACCOUNT_ID` | yes | Shown by `npx wrangler whoami` |
| `FAA_CLIENT_ID` / `FAA_CLIENT_SECRET` | optional | NOTAMs and FICONs |
| `WU_API_KEY` | optional | Weather Underground PWS |

## Regions

`regions.py` holds the region-specific settings: hubs, satellite sector, surface charts, radars, forecast offices and the AI prompt. Choose one with `--region`.

| Region | Command | Live URL | Workflow / Worker config |
|---|---|---|---|
| Alaska (Ryan Air network) | `python3 briefing.py --region alaska` | https://ra-briefing.ra-briefing.workers.dev | `briefing.yml` / `wrangler.jsonc` |
| Denver area | `python3 briefing.py --region denver` | https://denver-briefing.ra-briefing.workers.dev | `denver.yml` / `wrangler.denver.jsonc` |

Each region has its own GitHub workflow and Cloudflare Worker. Each Worker needs its own `GH_DISPATCH_TOKEN` secret for on-time 5-minute refreshes; one token with Actions read/write on this repo covers both. Denver uses GOES-19 (Southern Rockies sector), WPC surface analyses and forecasts, domestic SIGMETs, ZDV Center Weather Advisories, G-AIRMETs and the BOU/PUB/GJT forecast discussions.

### Alaska mail routing

Alaska villages and stations are grouped the way Bypass Mail is routed: hub to bush point, as listed in USPS Handbook PO-508, Appendix A, Attachment D (March 2012 edition). They're matched by airport/mail stop code (`mail_routes` in `regions.py`). Savoonga is its own mail hub (it serves Gambell). Stations that route through mail hubs outside the briefing (Galena, McGrath, Dillingham and others) are left out. Anything not on a route (Kodiak villages, Diomede, non-village stations) is grouped under the nearest hub and tagged "nearby".
