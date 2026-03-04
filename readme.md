# KPTV FAST Streams

A high-performance streaming service aggregator that combines multiple free streaming platforms into a single M3U playlist and EPG. Perfect for use with Channels DVR, Plex, Jellyfin, or any IPTV client.

## Overview

KPTV FAST Streams pulls live TV channels from 20+ free streaming services and presents them as a unified M3U playlist with a matching XMLTV EPG feed. All channel fetching runs concurrently, startup cache warming means your first request is instant, and background refresh keeps everything current without any downtime.

---

## Features

- **20+ Streaming Providers** — Pluto, Plex, Samsung, Tubi, Xumo, DistroTV, LG, Stirr, Philo, Vizio, Roku, LocalNow, TCL, TCL Plus, Fire TV, Xiaomi, Tablo, Whale TV+, Git IPTV (iptv-org), Git Free TV (Free-TV)
- **Unified EPG** — Aggregates external XMLTV sources into a single endpoint; no provider-specific configuration needed
- **Concurrent Fetching** — All providers are queried in parallel via `ThreadPoolExecutor`
- **Smart Caching** — 2-hour default cache with background pre-refresh at 75% TTL
- **Startup Cache Warming** — Channels and EPG pre-loaded at boot so the first request is instant
- **Duplicate Removal** — Cross-provider deduplication by name + stream URL
- **Regex Filtering** — Include/exclude channels or groups by regex pattern
- **Country Filtering** — Filter Git IPTV and LG providers by country code, name, or 3-letter ISO code
- **Multiple Output Formats** — M3U playlist, XMLTV EPG (plain + gzip), and JSON
- **Debug Mode** — Verbose per-provider logging with function-level timing
- **Health Monitoring** — Built-in `/status` endpoint compatible with Docker health checks
- **Non-root Container** — Runs as `appuser` (UID 1000) out of the box

---

## Supported Providers

| Provider | Key | Approx. Channels | Auth Required | EPG Source |
|---|---|---|---|---|
| Pluto TV | `pluto` | ~400 | Optional (credentials) | i.mjh.nz |
| Plex | `plex` | ~650 | None (anonymous token) | i.mjh.nz |
| Samsung TV Plus | `samsung` | ~420 | None | i.mjh.nz |
| Tubi | `tubi` | ~50+ | None | BuddyChewChew repo |
| Xumo | `xumo` | ~85 | None | BuddyChewChew repo |
| DistroTV | `distrotv` | ~150+ | None | EPGShare01 |
| LG Channels | `lg` | ~100–500+ | None | EPGShare01 |
| Stirr | `stirr` | ~140+ | None | i.mjh.nz |
| Philo | `philo` | ~110+ | Session cookies | None |
| Vizio WatchFree+ | `vizio` | ~300+ | None | None |
| The Roku Channel | `roku` | ~300+ | None | None |
| Local Now | `localnow` | ~400+ | None | BuddyChewChew repo |
| TCL TV | `tcl` | ~500+ | None | None |
| TCL TV Plus | `tclplus` | ~400+ | None | None |
| Fire TV | `firetv` | ~50+ | None | None |
| Xiaomi TV+ | `xiaomi` | ~200+ | None | None |
| Tablo | `tablo` | ~100+ | None | None |
| Whale TV+ | `whale` | ~100+ | None (API token) | None |
| Git IPTV (iptv-org) | `git_iptv` | ~500–2000+ | None | None |
| Git Free TV (Free-TV) | `git_freetv` | ~100–500+ | None | None |

Channel counts vary by region and available content at fetch time.

---

## Quick Start

### Docker Compose (Recommended)

Copy `docker-compose-example.yaml` to `docker-compose.yml` and edit as needed:

```yaml
services:
  kptv_fast:
    image: ghcr.io/kpirnie/kptv-fast:latest
    container_name: kptv_fast
    ports:
      - 8080:8080
    environment:
      - CACHE_DURATION=7200
      - WARM_CACHE_ON_STARTUP=true
      - STARTUP_CACHE_DELAY=10
      - WARM_EPG_ON_STARTUP=true
      - MAX_WORKERS=5
      - PROVIDER_TIMEOUT=60
      - ENABLED_PROVIDERS=all
      - GIT_COUNTRY=us,ca,uk
      - LG_COUNTRY=us,ca,uk
      - WHALE_COUNTRY=us
      - DEBUG=false
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "wget", "--quiet", "--tries=1", "--spider", "http://localhost:8080/status"]
      interval: 30s
      timeout: 10s
      retries: 3
      start_period: 40s
```

```bash
docker compose up -d
```

### Manual / Local Run

```bash
git clone https://github.com/kpirnie/kptv-fast
cd kptv-fast
pip install -r requirements.txt
DEBUG=true GIT_COUNTRY=us,ca LG_COUNTRY=us,ca python app.py
```

---

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /` or `GET /status` | HTML status page with per-provider stats and cache info |
| `GET /playlist` | M3U8 playlist of all channels |
| `GET /epg` | Combined XMLTV EPG (plain XML) |
| `GET /epg` *(with `Accept-Encoding: gzip`)* | Combined XMLTV EPG (gzip-compressed) |
| `GET /channels` | All channels as JSON |
| `GET /debug` | Debug JSON with provider stats, cache status, and runtime info |
| `GET /refresh` | Force-clear cache and re-fetch all channels |
| `GET /clear_cache` | Clear channels + EPG cache without re-fetching |

Add `?refresh=1` to `/` or `/status` to force a live channel count instead of reading the cache.

### IPTV Client Setup

| Client | M3U URL | EPG URL |
|---|---|---|
| Channels DVR | `http://your-host:8080/playlist` | `http://your-host:8080/epg` |
| Plex | `http://your-host:8080/playlist` | `http://your-host:8080/epg` |
| Jellyfin | `http://your-host:8080/playlist` | `http://your-host:8080/epg` |
| Kodi (PVR IPTV Simple) | `http://your-host:8080/playlist` | `http://your-host:8080/epg` |
| VLC | `vlc http://your-host:8080/playlist` | — |

---

## Configuration

All configuration is done via environment variables.

### Core Settings

| Variable | Default | Description |
|---|---|---|
| `DEBUG` | `false` | Enable verbose logging (function names, line numbers, stack traces) |
| `ENABLED_PROVIDERS` | `all` | Comma-separated list of provider keys to enable, or `all` |
| `CACHE_DURATION` | `7200` | Channel cache TTL in seconds |
| `MAX_WORKERS` | `5` | Max concurrent provider fetch threads |
| `PROVIDER_TIMEOUT` | `45` | Per-provider hard timeout in seconds |

### Startup & Cache

| Variable | Default | Description |
|---|---|---|
| `WARM_CACHE_ON_STARTUP` | `true` | Pre-load channel cache on boot |
| `WARM_EPG_ON_STARTUP` | `true` | Pre-load EPG cache on boot |
| `STARTUP_CACHE_DELAY` | `10` | Seconds to wait before beginning cache warm |

### Content Filtering

All values are Python-compatible regex patterns matched case-insensitively.

| Variable | Default | Description |
|---|---|---|
| `CHANNEL_NAME_INCLUDE` | `""` | Only include channels whose name matches |
| `CHANNEL_NAME_EXCLUDE` | `""` | Exclude channels whose name matches |
| `GROUP_INCLUDE` | `""` | Only include channels whose group matches |
| `GROUP_EXCLUDE` | `""` | Exclude channels whose group matches |

Examples:

```yaml
# News channels only
- CHANNEL_NAME_INCLUDE=news|cnn|fox|msnbc|abc news|nbc news

# Exclude adult/shopping
- GROUP_EXCLUDE=adult|shopping|infomercial

# US sports and entertainment
- GROUP_INCLUDE=sports|entertainment|comedy
```

### Provider-Specific Settings

| Variable | Default | Description |
|---|---|---|
| `PLUTO_REGION` | `us_west` | `us_east`, `us_west`, `uk`, `ca`, `fr` |
| `PLUTO_USERNAME` | `""` | Optional Pluto account username |
| `PLUTO_PASSWORD` | `""` | Optional Pluto account password |
| `PLEX_REGION` | `local` | `local`, `clt`, `sea`, `dfw`, `nyc`, `la` |
| `SAMSUNG_REGION` | `us` | Any region code present in Samsung's feed, or `all` |
| `PHILO_SESSION_ID` | `""` | Browser cookie `_session_id` from www.philo.com |
| `PHILO_HASHED_SESSION_ID` | `""` | Browser cookie `hashed_session_id` from www.philo.com |
| `WHALE_COUNTRY` | `us` | Comma-separated country codes for Whale TV+ (e.g. `us,gb,ca`) |
| `WHALE_LANG` | `en` | Language code for Whale TV+ metadata |
| `GIT_COUNTRY` | `""` | Country filter for `git_iptv` and `git_freetv` (see below) |
| `LG_COUNTRY` | `us` | Country filter for `lg` provider (see below) |
| `GITHUB_TOKEN` | `""` | GitHub personal access token for higher API rate limits |

### Country Filtering

`GIT_COUNTRY` and `LG_COUNTRY` accept flexible, comma-separated values. All of the following formats are supported:

- 2-letter codes: `us`, `ca`, `uk`, `de`
- 3-letter codes: `usa`, `can`, `gbr`, `deu`
- Full names: `united states`, `canada`, `united kingdom`, `germany`
- Mixed: `us,canada,united kingdom,de`

`WHALE_COUNTRY` accepts 2-letter ISO country codes only (e.g. `us`, `gb`, `ca`). Each country triggers a separate API call; channels are deduplicated by stream URL.

```yaml
# North America
- GIT_COUNTRY=us,ca,mx
- LG_COUNTRY=us,ca,mx

# English-speaking world
- GIT_COUNTRY=us,uk,ca,au
- LG_COUNTRY=us,uk,ca,au

# No filter (all countries)
- GIT_COUNTRY=
- LG_COUNTRY=
```

### Philo Setup

Philo requires valid session cookies extracted from a logged-in browser session.

1. Open a browser and log in to [philo.com](https://www.philo.com)
2. Open DevTools → Application → Cookies → `www.philo.com`
3. Copy the values of `_session_id` and `hashed_session_id`
4. Set them as `PHILO_SESSION_ID` and `PHILO_HASHED_SESSION_ID`

Cookies expire periodically — update them if Philo channels stop loading.

---

## EPG System

The EPG aggregator downloads and combines multiple external XMLTV sources into a single `/epg` endpoint. Sources are fetched concurrently, deduplicated by channel ID, and cached for one hour.

| Source | Providers Covered |
|---|---|
| [i.mjh.nz](https://i.mjh.nz) | Pluto, Plex, Samsung, Stirr |
| [EPGShare01](https://epgshare01.online) | LG, DistroTV |
| [BuddyChewChew/tubi-scraper](https://github.com/BuddyChewChew/tubi-scraper) | Tubi |
| [BuddyChewChew/xumo-playlist-generator](https://github.com/BuddyChewChew/xumo-playlist-generator) | Xumo |
| [BuddyChewChew/localnow-playlist-generator](https://github.com/BuddyChewChew/localnow-playlist-generator) | LocalNow |

Providers not listed (Vizio, Roku, TCL, Tablo, Xiaomi, Fire TV, Whale TV+, Git IPTV, Git Free TV) do not have EPG data available through the aggregator.

---

## Performance

### Typical Metrics

| Metric | Value |
|---|---|
| Startup time (with warming) | 15–25 seconds |
| First request (warm cache) | < 100ms |
| Memory usage | ~200–350 MB |
| EPG build time | 10–30 seconds |
| EPG cache TTL | 1 hour |

### Tuning Tips

- **Increase `MAX_WORKERS`** if your host has headroom; 8–10 is reasonable for most setups
- **Reduce `PROVIDER_TIMEOUT`** to drop slow providers faster rather than waiting the full timeout
- **Use `ENABLED_PROVIDERS`** to skip providers you don't need — fewer providers means faster cache builds
- **Set `GITHUB_TOKEN`** if you enable `git_iptv` or `git_freetv` to avoid GitHub API rate limits
- **Use country filters** (`GIT_COUNTRY`, `LG_COUNTRY`, `WHALE_COUNTRY`) to reduce the number of API/M3U fetches

---

## Troubleshooting

### No channels loading

```bash
# View per-provider status
curl http://localhost:8080/debug

# Check container logs
docker compose logs -f kptv_fast

# Force a full refresh
curl http://localhost:8080/refresh
```

### EPG not loading in client

```bash
# Check if EPG builds successfully
curl -I http://localhost:8080/epg

# Clear and rebuild
curl http://localhost:8080/clear_cache
curl http://localhost:8080/refresh
```

### GitHub rate limiting (git_iptv / git_freetv)

Set a GitHub personal access token:

```yaml
- GITHUB_TOKEN=ghp_yourtokenhere
```

Unauthenticated requests are limited to 60/hour; a token raises this to 5,000/hour.

### Philo channels not loading

Philo session cookies expire. Re-extract `_session_id` and `hashed_session_id` from DevTools and update your environment variables, then restart the container.

### Slow startup / provider timing out

```yaml
- PROVIDER_TIMEOUT=30     # reduce to fail faster
- MAX_WORKERS=8           # increase parallelism
- ENABLED_PROVIDERS=pluto,plex,samsung,tubi,distrotv  # disable slow providers
```

### Enable full debug logging

```yaml
- DEBUG=true
```

This logs function names, line numbers, per-provider timings, and full stack traces on errors.

---

## Building from Source

```bash
git clone https://github.com/kpirnie/kptv-fast
cd kptv-fast
docker build -t kptv_fast:local .
```

The Dockerfile uses a two-stage Alpine build. The builder stage compiles Python packages with `gcc`/`cargo`; the runtime stage copies the venv and runs as non-root `appuser` (UID 1000).

---

## Adding a Provider

1. Create `providers/your_provider.py` inheriting from `BaseProvider`
2. Implement `get_channels() -> List[Dict]` and `get_epg_data() -> Dict`
3. Return channel dicts with at minimum `id`, `name`, `stream_url`; optionally `logo`, `group`, `number`, `description`, `language`
4. Register the class in `app.py` inside `_init_providers()`
5. Add the key to `ENABLED_PROVIDERS` docs and the provider table above
6. Test with `DEBUG=true python app.py`

---

## Security

- Container runs as non-root `appuser` (UID 1000) — no `--privileged` needed
- No credentials are stored on disk; all secrets are passed via environment variables
- Recommend placing behind a reverse proxy (nginx, Traefik, Caddy) for external access

```yaml
# Restrict to localhost only
ports:
  - "127.0.0.1:8080:8080"
```

---

## License

MIT License — see [LICENSE](LICENSE) for full text.

---

## Acknowledgments

- [@jgomez177](https://github.com/jgomez177) — Tubi, Plex, and Pluto TV implementation inspiration
- [@BuddyChewChew](https://github.com/BuddyChewChew) — Xumo implementation and EPG source repos
- [@matthuisman](https://github.com/matthuisman) — Samsung TV Plus implementation and i.mjh.nz EPG feeds
- [@iptv-org/iptv](https://github.com/iptv-org/iptv) — Community-maintained IPTV streams
- [@Free-TV/IPTV](https://github.com/Free-TV/IPTV) — Free TV IPTV repository
- [epgshare01.online](https://epgshare01.online) — Multi-provider EPG aggregation
- [apsattv.com](https://www.apsattv.com) — External M3U sources for Vizio, LG, and others
- [i.mjh.nz](https://i.mjh.nz) — Pluto, Plex, Samsung, and Stirr EPG feeds
- [watch.whaletvplus.com](https://watch.whaletvplus.com) — Whale TV+ free streaming

---

## Support & Issues

- **Bug reports and feature requests**: [GitHub Issues](https://github.com/kpirnie/kptv-fast/issues)
- **Discussions**: [GitHub Discussions](https://github.com/kpirnie/kptv-fast/discussions)

⭐ If this project is useful to you, a star on GitHub is always appreciated!