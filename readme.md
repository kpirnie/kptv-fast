# KPTV FAST Streams

A high-performance streaming service aggregator that combines multiple free streaming platforms into a single M3U playlist and EPG. Perfect for use with Channels DVR, Plex, or any IPTV client.

## 🎯 Overview

This application aggregates live TV channels from multiple streaming services into a unified playlist, making it easy to access thousands of free channels through your favorite IPTV client.

## ✨ Features

- **9 Streaming Providers**: Xumo, Tubi, Plex, Pluto TV, Samsung TV Plus, DistroTV, LG Channels, and GitHub-based IPTV repositories
- **Enhanced EPG System**: Automatic fallback to reliable external EPG sources when native implementations fail
- **High Performance**: Concurrent channel fetching with ~15-20 second startup time
- **Smart Caching**: 2-hour cache with background refresh to keep channels ready
- **Duplicate Removal**: Automatically removes duplicate channels across providers
- **Flexible Filtering**: Regex-based channel and group filtering
- **Country Filtering**: Filter Git-based and LG providers by country codes or names
- **Multiple Formats**: M3U playlist, XMLTV EPG, and JSON channel data
- **Debug Mode**: Comprehensive logging for troubleshooting
- **Health Monitoring**: Built-in health checks and status endpoints
- **Docker Ready**: Fully containerized with Docker Compose

## 📺 Supported Providers

| Provider | Channels* | Authentication | EPG Source | Notes |
|----------|-----------|----------------|------------|-------|
| **Pluto TV** | ~400 | None | Native + Fallback | Largest selection, reliable |
| **Plex** | ~650 | None | Native + Fallback | High-quality channels |
| **Samsung TV Plus** | ~420 | None | Native + Fallback | Good variety |
| **Xumo** | ~85 | None | Fallback Only | Optimized for speed |
| **Tubi** | ~50+ | None | Native + Fallback | Anonymous access |
| **DistroTV** | ~150+ | None | Fallback Only | Free multicultural content |
| **LG Channels** | ~100-500+ | None | Fallback Only | Country-specific, multiple regions |
| **Stirr** | ~140+ | None | Native + Fallback Only | Anonymous access |
| **Git IPTV (iptv-org)** | ~500-2000+ | None | None | Community-maintained, country-specific |
| **Git Free TV** | ~100-500+ | None | None | Free TV channels, various countries |

*Channel counts are approximate and vary by region and filtering

## 🚀 Quick Start

### Docker Compose (Recommended)

1. **Create docker-compose.yml:**
```yaml
version: '3.8'

services:
  unified-streaming:
    image: ghcr.io/kpirnie/kptv-fast:latest
    ports:
      - "7777:7777"
    environment:
      - DEBUG=false
      - CACHE_DURATION=7200
      - WARM_CACHE_ON_STARTUP=true
      - WARM_EPG_ON_STARTUP=true
      - ENABLED_PROVIDERS=all
      - GIT_COUNTRY=us,ca,uk  # Filter Git providers to these countries
      - LG_COUNTRY=us,ca,uk   # Filter LG provider to these countries
    restart: unless-stopped
    healthcheck:
      test: ["CMD", "curl", "-f", "http://localhost:7777/status"]
      interval: 30s
      timeout: 10s
      retries: 3
```

2. **Start the service:**
```bash
docker-compose up -d
```

3. **Access your content:**
   - Status page: `http://localhost:7777/status`
   - M3U playlist: `http://localhost:7777/playlist`
   - EPG: `http://localhost:7777/epg`
   - EPG GZ: `http://localhost:7777/epggz`

### Manual Build

```bash
git clone https://github.com/kpirnie/kptv-fast
cd kptv-fast
docker-compose build
docker-compose up -d
```

## ⚙️ Configuration

### Environment Variables

#### Basic Settings
| Variable | Default | Description |
|----------|---------|-------------|
| `PORT` | `7777` | HTTP server port |
| `DEBUG` | `false` | Enable verbose logging |
| `ENABLED_PROVIDERS` | `all` | Comma-separated list of providers to enable |

#### Performance Settings
| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_DURATION` | `7200` | Cache duration in seconds (2 hours) |
| `MAX_WORKERS` | `5` | Concurrent provider fetching threads |
| `PROVIDER_TIMEOUT` | `45` | Per-provider timeout in seconds |

#### Startup Optimization
| Variable | Default | Description |
|----------|---------|-------------|
| `WARM_CACHE_ON_STARTUP` | `true` | Pre-load channels on startup |
| `WARM_EPG_ON_STARTUP` | `true` | Pre-load EPG data on startup |
| `STARTUP_CACHE_DELAY` | `10` | Delay before cache warming (seconds) |

#### Content Filtering
| Variable | Default | Description |
|----------|---------|-------------|
| `CHANNEL_NAME_INCLUDE` | `""` | Regex to include channels by name |
| `CHANNEL_NAME_EXCLUDE` | `""` | Regex to exclude channels by name |
| `GROUP_INCLUDE` | `""` | Regex to include channels by group |
| `GROUP_EXCLUDE` | `""` | Regex to exclude channels by group |

#### Provider-Specific Settings
| Variable | Default | Description |
|----------|---------|-------------|
| `PLUTO_REGION` | `us_west` | Pluto TV region (us_west, us_east, uk, ca, fr) |
| `PLEX_REGION` | `local` | Plex region |
| `SAMSUNG_REGION` | `us` | Samsung TV Plus region |

#### Git Provider Settings
| Variable | Default | Description |
|----------|---------|-------------|
| `GIT_COUNTRY` | `""` | Country filter for Git providers (comma-separated) |
| `GITHUB_TOKEN` | `""` | GitHub API token for higher rate limits (optional) |

#### LG Provider Settings
| Variable | Default | Description |
|----------|---------|-------------|
| `LG_COUNTRY` | `us` | Country filter for LG provider (comma-separated) |

### Provider Country Filtering

Both `GIT_COUNTRY` and `LG_COUNTRY` environment variables support flexible country filtering:

#### Supported Formats:
- **2-letter codes**: `us,ca,uk,de,fr`
- **3-letter codes**: `usa,can,gbr,deu,fra`
- **Full names**: `united states,canada,united kingdom,germany,france`
- **Mixed formats**: `us,canada,united kingdom,de`

#### Examples:
```yaml
# North America only
- GIT_COUNTRY=us,ca,mx
- LG_COUNTRY=us,ca,mx

# English-speaking countries
- GIT_COUNTRY=us,uk,ca,au
- LG_COUNTRY=us,uk,ca,au

# Major European countries
- GIT_COUNTRY=uk,de,fr,it,es,nl
- LG_COUNTRY=uk,de,fr,it,es,nl

# All countries (no filter)
- GIT_COUNTRY=
- LG_COUNTRY=

# Single country
- GIT_COUNTRY=united states
- LG_COUNTRY=us
```

### Example Configurations

#### Basic Setup
```yaml
environment:
  - PORT=7777
  - DEBUG=false
  - CACHE_DURATION=7200
```

#### Performance Optimized
```yaml
environment:
  - MAX_WORKERS=8
  - PROVIDER_TIMEOUT=45
  - WARM_CACHE_ON_STARTUP=true
  - WARM_EPG_ON_STARTUP=true
  - STARTUP_CACHE_DELAY=5
```

#### Specific Providers Only
```yaml
environment:
  - ENABLED_PROVIDERS=pluto,plex,samsung,distrotv,lg
  - GIT_COUNTRY=us,ca,uk
  - LG_COUNTRY=us,ca,uk
```

#### Filtered Setup (News Channels Only)
```yaml
environment:
  - CHANNEL_NAME_INCLUDE="news|cnn|fox|msnbc"
  - CACHE_DURATION=3600
```

## 📡 Enhanced EPG System

The application features an advanced EPG system with automatic fallback support:

### EPG Sources by Provider:
- **Pluto TV**: Native API + i.mjh.nz fallback
- **Plex**: Native API + i.mjh.nz fallback
- **Samsung TV Plus**: Native API + i.mjh.nz fallback
- **Tubi**: Native scraping + BuddyChewChew repository fallback
- **Xumo**: BuddyChewChew repository fallback
- **DistroTV**: EPGShare01 + vraomoturi repository fallback
- **LG Channels**: EPGShare01 fallback
- **Git Providers**: No EPG (playlist only)

### External EPG Sources:
- **i.mjh.nz**: Comprehensive EPG for major providers
- **EPGShare01**: Multi-provider EPG aggregation
- **BuddyChewChew**: Specialized repositories for Tubi/Xumo
- **vraomoturi**: DistroTV-specific EPG data

### How It Works:
1. **Native First**: Attempts to use each provider's native EPG API
2. **Automatic Fallback**: If native fails, automatically uses external sources
3. **Intelligent Mapping**: Maps external channel IDs to internal format
4. **Caching**: Caches external EPG data to reduce requests
5. **Error Recovery**: Graceful degradation when all sources fail

## 🌐 API Endpoints

### Content Endpoints
- **`GET /playlist`** - M3U8 playlist with all channels
- **`GET /epg`** - XMLTV EPG data
- **`GET /epggz`** - Compressed XMLTV EPG data
- **`GET /channels`** - JSON formatted channel list

### Management Endpoints
- **`GET /status`** - HTML status page with statistics
- **`GET /debug`** - JSON debug information
- **`GET /refresh`** - Force cache refresh: set a cronjob to curl/wget this endpoint to setup a regular refresh
- **`GET /clear_cache`** - Clear all cached data

### Status Page
The status page (`/status`) provides:
- Total channel count and EPG coverage
- Per-provider statistics with EPG status
- Cache status and performance metrics
- Enhanced EPG system status
- Git and LG country filter status
- Quick links to all endpoints

## 📊 Performance

### Typical Performance Metrics
- **Startup Time**: 15-20 seconds with cache warming
- **First Request**: Instant (with cache warming enabled)
- **Subsequent Requests**: <100ms (cached)
- **Memory Usage**: ~200-300MB
- **CPU Usage**: Low (mostly I/O bound)

### Enhanced EPG Performance
- **External EPG Sources**: ~2-5 seconds per provider (cached for 1 hour)
- **Native EPG APIs**: ~3-10 seconds per provider
- **Fallback Activation**: Automatic, no performance penalty
- **Channel ID Mapping**: Minimal performance impact

### Optimization Tips

1. **Enable Cache Warming**: Set `WARM_CACHE_ON_STARTUP=true` and `WARM_EPG_ON_STARTUP=true`
2. **Tune Worker Count**: Adjust `MAX_WORKERS` based on your server
3. **Regional Optimization**: Use closer regions for better performance
4. **Filter Channels**: Use regex filters to reduce channel count
5. **Country Filtering**: Use `GIT_COUNTRY` and `LG_COUNTRY` to limit scope
6. **GitHub Token**: Set `GITHUB_TOKEN` for higher API rate limits
7. **Monitor Debug Logs**: Use `DEBUG=true` to identify slow providers

## 🔧 Troubleshooting

### Common Issues

#### No Channels Loading
```bash
# Check provider status
curl http://localhost:7777/debug

# Check logs
docker-compose logs -f kptv-fast

# Force refresh
curl http://localhost:7777/refresh
```

#### Poor EPG Coverage
```bash
# Check EPG status in debug endpoint
curl http://localhost:7777/debug | jq '.epg_stats'

# Check enhanced EPG system status
curl http://localhost:7777/status
# Look for "Enhanced EPG system: ✅ Active"

# Force EPG refresh
curl http://localhost:7777/clear_cache
curl http://localhost:7777/refresh
```

#### Slow Performance
```bash
# Enable debug logging
docker-compose down
# Set DEBUG=true in docker-compose.yml
docker-compose up -d

# Check which provider is slow
docker-compose logs -f kptv-fast
```

#### Provider-Specific Issues

**DistroTV**: Scraping issues
```yaml
# DistroTV uses web scraping, may be affected by site changes
# Check logs for scraping errors
environment:
  - DEBUG=true  # Enable detailed scraping logs
```

**LG Channels**: Country filtering
```yaml
environment:
  - LG_COUNTRY=us,ca,uk  # Ensure countries exist
  - DEBUG=true           # Enable debug logs
```

**Git Providers**: GitHub rate limits
```yaml
environment:
  - GITHUB_TOKEN=your_github_personal_access_token
```

### Debug Mode

Enable comprehensive logging:
```yaml
environment:
  - DEBUG=true
```

This provides:
- Function-level logging
- Performance timings
- Error stack traces
- Provider-specific debug info
- EPG fallback system details
- Web scraping debug information

### Health Checks

The application includes built-in health monitoring:
```bash
# Check health
curl http://localhost:7777/status

# Docker health check
docker-compose ps
```

## 🏗️ Architecture

### Components
- **Flask Web Server**: HTTP API and status endpoints
- **Provider System**: Modular provider architecture with 9 providers
- **Enhanced EPG System**: Native + fallback EPG with external source integration
- **Caching Layer**: Redis-like in-memory caching with TTL
- **Background Tasks**: Cache warming and refresh threads for both channels and EPG
- **Concurrent Processing**: ThreadPoolExecutor for parallel fetching
- **Web Scraping**: BeautifulSoup + regex fallback for DistroTV
- **External Integration**: Multiple external M3U and EPG sources

### Provider Architecture
Each provider implements:
- `get_channels()`: Fetch channel list
- `get_epg_data()`: Fetch EPG data with automatic fallback
- Built-in validation and normalization
- Error handling and logging
- Caching support

### Enhanced EPG Architecture
EPG system features:
- Native provider EPG APIs
- Automatic fallback to external sources
- Channel ID mapping between formats
- Compressed EPG file handling
- Multi-source EPG aggregation
- Error recovery and graceful degradation

## 🤝 Integration Examples

### Channels DVR
1. Add source in Channels DVR
2. Use M3U URL: `http://your-server:7777/playlist`
3. Use EPG URL: `http://your-server:7777/epg`

### Plex
1. Install the IPTV plugin
2. Configure with M3U URL
3. Set EPG source

### VLC
```bash
vlc http://your-server:7777/playlist
```

### Kodi
1. Install PVR IPTV Simple Client
2. Set M3U path to your server URL
3. Configure EPG source

## 📝 Logging

### Log Levels
- **INFO**: General operations and status
- **WARNING**: Non-critical issues
- **ERROR**: Critical failures
- **DEBUG**: Detailed troubleshooting info

### Log Format
```
Production (DEBUG=false):
2025-08-22 13:00:00 - INFO - 🚀 1794 channels ready in 15.2s
2025-08-22 13:00:00 - INFO - 📺 Total EPG data collected for 1247 channels

Debug (DEBUG=true):
2025-08-22 13:00:00 - providers.distrotv - DEBUG - Scraping with BeautifulSoup
2025-08-22 13:00:00 - providers.lg - DEBUG - Fetching LG channels for us from https://www.apsattv.com/uslg.m3u
```

### Enhanced EPG Logs
```
EPG System Examples:
2025-08-22 13:00:00 - INFO - Native EPG successful for pluto: 387 channels
2025-08-22 13:00:00 - INFO - Using fallback EPG for xumo: 79 channels  
2025-08-22 13:00:00 - INFO - External EPG mapping for plex: 156/200 our channels matched
```

## 🔒 Security

### Best Practices
- Run as non-root user (built into Docker image)
- Use reverse proxy for external access
- Enable health checks
- Monitor logs for unusual activity
- Keep Docker images updated
- Use GitHub tokens for API access

### Network Security
```yaml
# Restrict to local network
ports:
  - "127.0.0.1:7777:7777"

# Or use reverse proxy
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.streaming.rule=Host(`streaming.local`)"
```

## 📈 Monitoring

### Prometheus Metrics (Future Enhancement)
The application is designed to support metrics collection:
- Channel count per provider
- EPG coverage per provider
- Request response times
- Cache hit/miss ratios
- Provider success rates
- External EPG source reliability

### Log Aggregation
For production deployments, consider:
- ELK Stack for log analysis
- Grafana for visualization
- Alert manager for notifications

## 🛠️ Development

### Local Development
```bash
# Clone repository
git clone https://github.com/kpirnie/kptv-fast
cd kptv-fast

# Install dependencies
pip install -r requirements.txt

# Run locally
DEBUG=true GIT_COUNTRY=us,ca LG_COUNTRY=us,ca python app.py
```

### Adding New Providers
1. Create new provider class inheriting from `BaseProvider`
2. Implement `get_channels()` and `get_epg_data()` methods
3. Add EPG fallback support in `utils/epg_fallback.py`
4. Add to provider imports in `app.py`
5. Test with debug mode enabled

### Contributing
1. Fork the repository
2. Create feature branch
3. Add tests for new functionality
4. Submit pull request

## 📄 License

MIT License - see LICENSE file for details.

## 🙏 Acknowledgments

- https://github.com/jgomez177 - Inspiration for Tubi, Plex, & Pluto implementations
- https://github.com/BuddyChewChew - Inspiration for the Xumo implementation and EPG sources
- https://github.com/matthuisman - Inspiration for the Samsung TVPlus implementation and i.mjh.nz EPG sources
- https://github.com/iptv-org/iptv - Community IPTV repository
- https://github.com/Free-TV/IPTV - Free TV IPTV repository
- https://epgshare01.online - EPG data aggregation
- https://www.apsattv.com - External M3U sources for DistroTV and LG
- All the streaming services for providing free content
- The open-source community for the excellent libraries used

## 📞 Support

- **Issues**: [GitHub Issues](https://github.com/kpirnie/kptv-fast/issues)

## 🔮 Roadmap

### Planned Features
- Additional streaming providers
- Enhanced EPG source redundancy

---

**⭐ If this project helps you, please give it a star on GitHub!**