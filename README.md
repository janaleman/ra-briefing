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

The briefing rebuilds every 30 minutes. The Cloudflare Worker (`src/index.js`, cron in `wrangler.jsonc`) starts the GitHub Actions workflow on time; this needs a fine-grained GitHub token, with Actions read/write on this repo only, stored as the Worker secret `GH_DISPATCH_TOKEN`. The workflow's own `*/30` schedule is a fallback. Runs skip themselves if the live briefing is under 20 minutes old. Each run deploys `out/` as the Worker's static assets. You can also start a run by hand from the Actions tab.

In CI the narrative comes from the Claude API (`claude-opus-5`, with server-side refusal fallback) instead of the local `claude` CLI. The radar images are sent inline.

| Repo secret | Required | Notes |
|---|---|---|
| `ANTHROPIC_API_KEY` | for narrative | From console.anthropic.com |
| `CLOUDFLARE_API_TOKEN` | yes | Token from the **Edit Cloudflare Workers** template |
| `CLOUDFLARE_ACCOUNT_ID` | yes | Shown by `npx wrangler whoami` |
| `FAA_CLIENT_ID` / `FAA_CLIENT_SECRET` | optional | NOTAMs and FICONs |
| `WU_API_KEY` | optional | Weather Underground PWS |
