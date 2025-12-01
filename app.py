#!/usr/bin/env python3
"""
Unified Streaming Service Aggregator - Optimized for Speed with Git IPTV Support
"""

import os
import re
import json
import gzip
import time
import threading
import traceback
import sys
import concurrent.futures
from flask import Flask, Response, request
from gevent.pywsgi import WSGIServer # type: ignore
from gevent import monkey # type: ignore
import xml.etree.ElementTree as ET


# Import logging configuration FIRST
from utils.logging_config import setup_logging, get_logger

monkey.patch_all()

# Set recursion limit early to prevent issues
sys.setrecursionlimit(2000)

# Setup logging globally
DEBUG_MODE = setup_logging()
logger = get_logger(__name__)

class UnifiedStreamingAggregator:

    def __init__(self):
        self.app = Flask(__name__)
        self.providers = {}
        self.channels_cache = {}
        self.epg_cache = {}
        self.cache_expiry = {}
        self.cache_lock = threading.Lock()
        
        # Configuration from environment variables
        self.port = int(os.getenv('PORT', 7777))
        self.cache_duration = int(os.getenv('CACHE_DURATION', 7200))  # 2 hours default (increased)
        self.enabled_providers = os.getenv('ENABLED_PROVIDERS', 'all').split(',')
        
        # Performance settings
        self.max_workers = int(os.getenv('MAX_WORKERS', 5))  # Concurrent provider fetches
        self.provider_timeout = int(os.getenv('PROVIDER_TIMEOUT', 45))  # Per-provider timeout
        
        # Startup cache warming setting
        self.warm_cache_on_startup = os.getenv('WARM_CACHE_ON_STARTUP', 'true').lower() == 'true'
        self.warm_epg_on_startup = os.getenv('WARM_EPG_ON_STARTUP', 'true').lower() == 'true'  # Add this
        self.startup_delay = int(os.getenv('STARTUP_CACHE_DELAY', 10))

        # Debug mode
        self.debug_mode = DEBUG_MODE
        
        # Filter configuration
        self.channel_name_include = os.getenv('CHANNEL_NAME_INCLUDE', '')
        self.channel_name_exclude = os.getenv('CHANNEL_NAME_EXCLUDE', '')
        self.group_include = os.getenv('GROUP_INCLUDE', '')
        self.group_exclude = os.getenv('GROUP_EXCLUDE', '')
        
        # Git provider configuration
        self.git_country = os.getenv('GIT_COUNTRY', '')
        
        # Initialize providers
        self._init_providers()
        self._setup_routes()
        
        # Start background cache warming
        self._start_background_refresh()
        
        # Start startup cache warming
        if self.warm_cache_on_startup:
            self._start_startup_cache_warming()


    def _start_startup_cache_warming(self):
        """Start cache warming on startup with progress tracking"""
        def startup_cache_warmer():
            try:
                # Wait for providers to be ready
                logger.info("⏳ Waiting for providers to initialize...")
                max_wait = 60
                wait_time = 0
                
                while wait_time < max_wait and len(self.providers) == 0:
                    time.sleep(2)
                    wait_time += 2
                    if wait_time % 10 == 0:  # Log every 10 seconds
                        logger.info(f"Still waiting for providers... ({wait_time}s)")
                
                if len(self.providers) == 0:
                    logger.warning("❌ No providers available for cache warming")
                    return
                
                logger.info(f"✅ Found {len(self.providers)} providers: {', '.join(self.providers.keys())}")
                
                # Additional delay
                if self.startup_delay > 0:
                    logger.info(f"⏳ Waiting {self.startup_delay}s for full initialization...")
                    time.sleep(self.startup_delay)
                
                logger.info("🔥 Starting startup cache warming...")
                start_time = time.time()
                
                # Warm channels cache first
                logger.info("📺 Warming channels cache...")
                channels_start_time = time.time()
                all_channels = self._get_all_channels_concurrent()
                channels_elapsed = time.time() - channels_start_time
                logger.info(f"✅ Channels cache warmed: {len(all_channels)} channels in {channels_elapsed:.1f}s")
                
                # Warm EPG cache if enabled
                all_epg_data = {}
                if self.warm_epg_on_startup:
                    logger.info("📡 Warming EPG cache...")
                    epg_start_time = time.time()
                    all_epg_data = self._get_all_epg_data()
                    epg_elapsed = time.time() - epg_start_time
                    logger.info(f"✅ EPG cache warmed: {len(all_epg_data)} channels with EPG in {epg_elapsed:.1f}s")
                else:
                    logger.info("⏭️ EPG cache warming disabled (WARM_EPG_ON_STARTUP=false)")
                
                total_elapsed = time.time() - start_time
                logger.info(f"🎉 Startup cache warming completed!")
                logger.info(f"📊 Total: {len(all_channels)} channels, {len(all_epg_data)} with EPG in {total_elapsed:.2f}s")
                logger.info(f"🚀 First requests will now be instant!")
                
            except Exception as e:
                logger.error(f"❌ Startup cache warming failed: {e}")
                logger.debug(traceback.format_exc())
        
        startup_thread = threading.Thread(target=startup_cache_warmer, daemon=True)
        startup_thread.start()
        logger.info("🌟 Startup cache warming scheduled")

    def _init_providers(self):
        """Initialize all available providers with better error handling"""
        # Import providers individually to isolate issues
        available_providers = {}
        
        # Try to import each provider individually
        try:
            from providers.xumo_provider import XumoProvider
            available_providers['xumo'] = XumoProvider
            logger.info("Successfully imported XumoProvider")
        except Exception as e:
            logger.error(f"Failed to import XumoProvider: {e}")
        
        try:
            from providers.tubi_provider import TubiProvider
            available_providers['tubi'] = TubiProvider
            logger.info("Successfully imported TubiProvider")
        except Exception as e:
            logger.error(f"Failed to import TubiProvider: {e}")
        
        try:
            from providers.pluto_provider import PlutoProvider
            available_providers['pluto'] = PlutoProvider
            logger.info("Successfully imported PlutoProvider")
        except Exception as e:
            logger.error(f"Failed to import PlutoProvider: {e}")
        
        try:
            from providers.plex_provider import PlexProvider
            available_providers['plex'] = PlexProvider
            logger.info("Successfully imported PlexProvider")
        except Exception as e:
            logger.error(f"Failed to import PlexProvider: {e}")
        
        try:
            from providers.samsung_provider import SamsungProvider
            available_providers['samsung'] = SamsungProvider
            logger.info("Successfully imported SamsungProvider")
        except Exception as e:
            logger.error(f"Failed to import SamsungProvider: {e}")

        try:
            from providers.distrotv_provider import DistroTVProvider
            available_providers['distrotv'] = DistroTVProvider
            logger.info("Successfully imported DistroTVProvider")
        except Exception as e:
            logger.error(f"Failed to import DistroTVProvider: {e}")

        try:
            from providers.lg_provider import LGProvider
            available_providers['lg'] = LGProvider
            logger.info("Successfully imported LGProvider")
        except Exception as e:
            logger.error(f"Failed to import LGProvider: {e}")
        
        try:
            from providers.git_providers import GitIptvProvider, GitFreetvProvider
            available_providers['git_iptv'] = GitIptvProvider
            available_providers['git_freetv'] = GitFreetvProvider
            logger.info("Successfully imported GitIptvProvider and GitFreetvProvider")
        except Exception as e:
            logger.error(f"Failed to import git providers: {e}")

        try:
            from providers.stirr_provider import StirrProvider
            available_providers['stirr'] = StirrProvider
            logger.info("Successfully imported StirrProvider")
        except Exception as e:
            logger.error(f"Failed to import StirrProvider: {e}")
        
        # Initialize providers
        for name, provider_class in available_providers.items():
            if self.enabled_providers == ['all'] or name in self.enabled_providers:
                try:
                    logger.info(f"Initializing {name} provider...")
                    self.providers[name] = provider_class()
                    logger.info(f"Successfully initialized {name} provider")
                except Exception as e:
                    logger.error(f"Failed to initialize {name} provider: {e}")
                    logger.debug(traceback.format_exc())        

    def _setup_routes(self):
        """Setup Flask routes"""
        self.app.route('/playlist')(self.get_playlist)
        self.app.route('/epg')(self.get_epg_xml)
        self.app.route('/epggz')(self.get_epg_xml_gz)
        self.app.route('/channels')(self.get_channels_json)
        self.app.route('/status')(self.get_status)
        self.app.route('/clear_cache')(self.clear_cache)
        self.app.route('/debug')(self.get_debug_info)
        self.app.route('/refresh')(self.force_refresh)
        
    def _start_background_refresh(self):
        """Start background thread to keep cache warm"""
        def background_refresher():
            while True:
                try:
                    # Wait 5 minutes before first refresh
                    time.sleep(300)
                    
                    # Check if caches are getting old (75% of cache duration)
                    with self.cache_lock:
                        now = time.time()
                        channels_age = now - (self.cache_expiry.get('all_channels', now) - self.cache_duration)
                        epg_age = now - (self.cache_expiry.get('all_epg', now) - self.cache_duration)
                        
                        channels_needs_refresh = channels_age > (self.cache_duration * 0.75)
                        epg_needs_refresh = epg_age > (self.cache_duration * 0.75)
                    
                    if channels_needs_refresh or epg_needs_refresh:
                        logger.info("🔄 Background refresh starting...")
                        start_time = time.time()
                        
                        if channels_needs_refresh:
                            logger.info("📺 Refreshing channels cache...")
                            self._get_all_channels_concurrent()
                        
                        if epg_needs_refresh:
                            logger.info("📡 Refreshing EPG cache...")
                            self._get_all_epg_data()
                        
                        elapsed = time.time() - start_time
                        logger.info(f"✅ Background refresh completed in {elapsed:.1f}s")
                    
                except Exception as e:
                    logger.error(f"Background refresh error: {e}")
                
                # Check every 15 minutes
                time.sleep(900)
        
        refresh_thread = threading.Thread(target=background_refresher, daemon=True)
        refresh_thread.start()
        logger.info("🔄 Background refresh thread started")
        
    def _is_cache_valid(self, cache_key):
        """Check if cache is still valid"""
        return (cache_key in self.cache_expiry and 
                time.time() < self.cache_expiry[cache_key])
    
    def _apply_filters(self, channels):
        """Apply regex filters to channels"""
        if not any([self.channel_name_include, self.channel_name_exclude, 
                   self.group_include, self.group_exclude]):
            return channels  # No filters to apply
            
        filtered_channels = []
        
        for channel in channels:
            name = channel.get('name', '')
            group = channel.get('group', '')
            
            # Apply name filters
            if self.channel_name_include and not re.search(self.channel_name_include, name, re.IGNORECASE):
                continue
            if self.channel_name_exclude and re.search(self.channel_name_exclude, name, re.IGNORECASE):
                continue
                
            # Apply group filters  
            if self.group_include and not re.search(self.group_include, group, re.IGNORECASE):
                continue
            if self.group_exclude and re.search(self.group_exclude, group, re.IGNORECASE):
                continue
                
            filtered_channels.append(channel)
            
        return filtered_channels
    
    def _remove_duplicates(self, channels):
        """Remove duplicate channels based on name and stream URL"""
        seen = set()
        unique_channels = []
        
        for channel in channels:
            # Create a key based on normalized name and stream URL
            key = (
                channel.get('name', '').lower().strip(),
                channel.get('stream_url', '')
            )
            
            if key not in seen and key[0] and key[1]:  # Ensure name and URL exist
                seen.add(key)
                unique_channels.append(channel)
                
        logger.info(f"Removed {len(channels) - len(unique_channels)} duplicate channels")
        return unique_channels
    
    def _fetch_provider_channels(self, provider_name, provider):
        """Fetch channels from a single provider with timeout"""
        try:
            if self.debug_mode:
                logger.debug(f"Fetching channels from {provider_name}")
            start_time = time.time()
            
            # Use a more robust timeout mechanism
            import signal
            
            def timeout_handler(signum, frame):
                raise TimeoutError(f"Provider {provider_name} timed out")
            
            provider_channels = []
            
            try:
                # Set up timeout for the whole operation
                if hasattr(signal, 'SIGALRM'):  # Unix systems
                    signal.signal(signal.SIGALRM, timeout_handler)
                    signal.alarm(self.provider_timeout)
                
                provider_channels = provider.get_channels()
                
                if hasattr(signal, 'SIGALRM'):
                    signal.alarm(0)  # Cancel the alarm
                    
            except TimeoutError:
                logger.warning(f"⏰ {provider_name} timed out after {self.provider_timeout}s")
                return []
            except Exception as e:
                logger.error(f"❌ {provider_name} failed: {e}")
                if self.debug_mode:
                    logger.debug(traceback.format_exc())
                return []
            
            elapsed = time.time() - start_time
            
            if provider_channels:
                if self.debug_mode:
                    logger.debug(f"✅ {provider_name}: {len(provider_channels)} channels in {elapsed:.1f}s")
                else:
                    logger.info(f"✅ {provider_name}: {len(provider_channels)} channels")
                return provider_channels
            else:
                logger.warning(f"⚠️  {provider_name}: No channels found in {elapsed:.1f}s")
                return []
                
        except Exception as e:
            logger.error(f"❌ Error fetching channels from {provider_name}: {e}")
            if self.debug_mode:
                logger.debug(traceback.format_exc())
            return []
    
    def _get_all_channels_concurrent(self):
        """Get channels from all providers concurrently"""
        cache_key = 'all_channels'
        
        with self.cache_lock:
            if self._is_cache_valid(cache_key):
                return self.channels_cache[cache_key]
        
        logger.info("Starting concurrent channel fetch from all providers")
        start_time = time.time()
        
        all_channels = []
        channel_number = 1
        
        # Use ThreadPoolExecutor to fetch from all providers concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            # Submit all provider tasks
            future_to_provider = {
                executor.submit(self._fetch_provider_channels, name, provider): name 
                for name, provider in self.providers.items()
            }
            
            # Collect results as they complete
            for future in concurrent.futures.as_completed(future_to_provider):
                provider_name = future_to_provider[future]
                try:
                    provider_channels = future.result()
                    
                    if provider_channels:
                        # Add provider info and assign channel numbers
                        for channel in provider_channels:
                            channel['provider'] = provider_name
                            channel['channel_number'] = channel.get('number', channel_number)
                            channel_number += 1
                            
                        all_channels.extend(provider_channels)
                        
                except Exception as e:
                    logger.error(f"Error processing results from {provider_name}: {e}")
        
        # Apply filters and remove duplicates
        all_channels = self._apply_filters(all_channels)
        all_channels = self._remove_duplicates(all_channels)
        
        # Sort by channel number
        all_channels.sort(key=lambda x: x.get('channel_number', 999999))
        
        # Cache the results
        with self.cache_lock:
            self.channels_cache[cache_key] = all_channels
            self.cache_expiry[cache_key] = time.time() + self.cache_duration
            
        elapsed = time.time() - start_time
        logger.info(f"Completed concurrent fetch: {len(all_channels)} channels in {elapsed:.2f}s")
        return all_channels
    
    def _get_all_channels(self):
        """Get channels using concurrent method"""
        return self._get_all_channels_concurrent()
    
    def _get_all_epg_data(self):
        """Get EPG data from all providers concurrently"""
        cache_key = 'all_epg'
        
        with self.cache_lock:
            if self._is_cache_valid(cache_key):
                return self.epg_cache[cache_key]
        
        all_epg_data = {}
        
        # Use concurrent execution for EPG fetching too
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_provider = {
                executor.submit(provider.get_epg_data): provider_name 
                for provider_name, provider in self.providers.items()
            }
            
            for future in concurrent.futures.as_completed(future_to_provider):
                provider_name = future_to_provider[future]
                try:
                    provider_epg = future.result(timeout=self.provider_timeout)
                    if provider_epg:
                        all_epg_data.update(provider_epg)
                        logger.info(f"Got EPG data for {len(provider_epg)} channels from {provider_name}")
                except Exception as e:
                    logger.error(f"Error fetching EPG data from {provider_name}: {e}")
        
        # Cache the results
        with self.cache_lock:
            self.epg_cache[cache_key] = all_epg_data
            self.cache_expiry[cache_key] = time.time() + self.cache_duration
            
        return all_epg_data
    
    def get_playlist(self):
        """Generate M3U playlist"""
        try:
            channels = self._get_all_channels()
            
            # Build M3U content more efficiently
            m3u_lines = ['#EXTM3U']
            
            for channel in channels:
                # Build EXTINF line more efficiently
                extinf_parts = ['#EXTINF:-1']
                
                # Add attributes in batch
                attrs = []
                if channel.get('id'):
                    attrs.append(f'tvg-id="{channel["id"]}"')
                if channel.get('name'):
                    attrs.append(f'tvg-name="{channel["name"]}"')
                if channel.get('logo'):
                    attrs.append(f'tvg-logo="{channel["logo"]}"')
                if channel.get('group'):
                    attrs.append(f'group-title="{channel["group"]}"')
                if channel.get('channel_number'):
                    attrs.append(f'tvg-chno="{channel["channel_number"]}"')
                if channel.get('provider'):
                    attrs.append(f'provider="{channel["provider"]}"')
                
                if attrs:
                    extinf_parts.extend(attrs)
                    
                extinf_line = ' '.join(extinf_parts) + f',{channel.get("name", "Unknown")}'
                m3u_lines.extend([extinf_line, channel.get('stream_url', ''), ''])
            
            m3u_content = '\n'.join(m3u_lines)
            
            return Response(
                m3u_content,
                mimetype='application/vnd.apple.mpegurl',
                headers={'Content-Disposition': 'attachment; filename=playlist.m3u'}
            )
            
        except Exception as e:
            logger.error(f"Error generating playlist: {e}")
            return Response(f"Error generating playlist: {e}", status=500)
    
    def get_epg_xml(self):
        """Generate EPG XML"""
        try:
            channels = self._get_all_channels()
            epg_data = self._get_all_epg_data()
            
            # Create XML structure
            root = ET.Element('tv')
            root.set('generator-info-name', 'KPTV FAST Streams')
            
            # Sort channels by name for EPG
            sorted_channels = sorted(channels, key=lambda x: x.get('name', '').lower())

            # Add channel elements
            for channel in sorted_channels:
                channel_elem = ET.SubElement(root, 'channel')
                channel_elem.set('id', str(channel.get('id', '')))
                
                display_name = ET.SubElement(channel_elem, 'display-name')
                # Format: "Channel Name - Provider"
                channel_name = channel.get('name', '')
                provider_name = channel.get('provider', '').title()
                if provider_name:
                    display_name.text = f"{channel_name} - {provider_name}"
                else:
                    display_name.text = channel_name
                
                if channel.get('logo'):
                    icon = ET.SubElement(channel_elem, 'icon')
                    icon.set('src', channel['logo'])
            
            # Add programme elements
            for channel_id, programmes in epg_data.items():
                for programme in programmes:
                    prog_elem = ET.SubElement(root, 'programme')
                    prog_elem.set('channel', str(channel_id))
                    prog_elem.set('start', programme.get('start', ''))
                    prog_elem.set('stop', programme.get('stop', ''))
                    
                    if programme.get('title'):
                        title = ET.SubElement(prog_elem, 'title')
                        title.text = programme['title']
                    
                    if programme.get('description'):
                        desc = ET.SubElement(prog_elem, 'desc')
                        desc.text = programme['description']
            
            # Convert to string
            xml_str = ET.tostring(root, encoding='unicode', method='xml')
            xml_content = f'<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE tv SYSTEM "xmltv.dtd">\n{xml_str}'
            
            return Response(
                xml_content,
                mimetype='application/xml',
                headers={'Content-Disposition': 'attachment; filename=epg.xml'}
            )
            
        except Exception as e:
            logger.error(f"Error generating EPG XML: {e}")
            return Response(f"Error generating EPG XML: {e}", status=500)
    
    def get_epg_xml_gz(self):
        """Generate compressed EPG XML"""
        try:
            # Get the XML content
            xml_response = self.get_epg_xml()
            xml_content = xml_response.get_data(as_text=True)
            
            # Compress it
            compressed_data = gzip.compress(xml_content.encode('utf-8'))
            
            return Response(
                compressed_data,
                mimetype='application/gzip',
                headers={'Content-Disposition': 'attachment; filename=epg.xml.gz'}
            )
            
        except Exception as e:
            logger.error(f"Error generating compressed EPG: {e}")
            return Response(f"Error generating compressed EPG: {e}", status=500)
    
    def get_channels_json(self):
        """Return channels as JSON"""
        try:
            channels = self._get_all_channels()
            return Response(
                json.dumps(channels, indent=2),
                mimetype='application/json'
            )
        except Exception as e:
            logger.error(f"Error generating channels JSON: {e}")
            return Response(f"Error generating channels JSON: {e}", status=500)
    
    def get_debug_info(self):
        """Return debug information"""
        try:
            import socket
            
            channels = self._get_all_channels()
            provider_stats = {}
            
            for channel in channels:
                provider = channel.get('provider', 'unknown')
                provider_stats[provider] = provider_stats.get(provider, 0) + 1
            
            with self.cache_lock:
                cache_info = {
                    'channels_cached': 'all_channels' in self.channels_cache,
                    'epg_cached': 'all_epg' in self.epg_cache,
                    'channels_cache_expires': self.cache_expiry.get('all_channels', 0),
                    'epg_cache_expires': self.cache_expiry.get('all_epg', 0),
                    'current_time': time.time()
                }
            
            debug_info = {
                'total_channels': len(channels),
                'provider_stats': provider_stats,
                'enabled_providers': list(self.providers.keys()),
                'git_country_filter': self.git_country,
                'python_version': sys.version,
                'recursion_limit': sys.getrecursionlimit(),
                'hostname': socket.gethostname(),
                'cache_status': cache_info,
                'performance_settings': {
                    'max_workers': self.max_workers,
                    'provider_timeout': self.provider_timeout,
                    'cache_duration': self.cache_duration
                }
            }
            
            return Response(
                json.dumps(debug_info, indent=2),
                mimetype='application/json'
            )
        except Exception as e:
            logger.error(f"Error generating debug info: {e}")
            return Response(f"Error generating debug info: {e}", status=500)
    
    def get_status(self):
        """Return status page"""
        try:
            channels = self._get_all_channels()
            provider_stats = {}
            
            for channel in channels:
                provider = channel.get('provider', 'unknown')
                provider_stats[provider] = provider_stats.get(provider, 0) + 1
            
            status_html = f"""
            <html>
            <head><title>KPTV FAST Streams</title><link rel="icon" type="image/png" href="https://cdn.kevp.us/tv/kptv-icon.png" /></head>
            <body>
                <h1>KPTV FAST Streams</h1>
                <h2>Status</h2>
                <p>Total Channels: {len(channels)}</p>
                <p>Initialized Providers: {', '.join(self.providers.keys())}</p>
                <p>Cache Duration: {self.cache_duration} seconds</p>
                <p>Max Workers: {self.max_workers}</p>
                <p>Git Country Filter: {self.git_country or 'None'}</p>
                <h3>Provider Statistics:</h3>
                <ul>
            """
            
            for provider, count in provider_stats.items():
                status_html += f"<li>{provider}: {count} channels</li>"
            
            status_html += """
                </ul>
                <h3>Endpoints:</h3>
                <ul>
                    <li><a href="/playlist">M3U Playlist</a></li>
                    <li><a href="/epg">EPG XML</a></li>
                    <li><a href="/epggz">EPG XML (Compressed)</a></li>
                    <li><a href="/channels">Channels JSON</a></li>
                    <li><a href="/debug">Debug Info (JSON)</a></li>
                    <li><a href="/refresh">Force Refresh</a></li>
                    <li><a href="/clear_cache">Clear Cache</a></li>
                </ul>
            </body>
            </html>
            """
            
            return Response(status_html, mimetype='text/html')
        except Exception as e:
            logger.error(f"Error generating status page: {e}")
            return Response(f"Error generating status page: {e}", status=500)
    
    def clear_cache(self):
        """Clear all caches"""
        try:
            with self.cache_lock:
                self.channels_cache.clear()
                self.epg_cache.clear()
                self.cache_expiry.clear()
                
            return Response("Cache cleared successfully", mimetype='text/plain')
        except Exception as e:
            logger.error(f"Error clearing cache: {e}")
            return Response(f"Error clearing cache: {e}", status=500)
    
    def force_refresh(self):
        """Force refresh of all data"""
        try:
            # Clear cache first
            with self.cache_lock:
                self.channels_cache.clear()
                self.epg_cache.clear()
                self.cache_expiry.clear()
            
            # Force new fetch
            start_time = time.time()
            
            logger.info("🔄 Force refreshing channels...")
            channels = self._get_all_channels()
            channels_elapsed = time.time() - start_time
            
            logger.info("🔄 Force refreshing EPG...")
            epg_start_time = time.time()
            epg_data = self._get_all_epg_data()
            epg_elapsed = time.time() - epg_start_time
            
            total_elapsed = time.time() - start_time
            
            return Response(
                f"Refresh completed in {total_elapsed:.2f}s. "
                f"Found {len(channels)} channels and {len(epg_data)} channels with EPG. "
                f"Channels: {channels_elapsed:.1f}s, EPG: {epg_elapsed:.1f}s",
                mimetype='text/plain'
            )
        except Exception as e:
            logger.error(f"Error forcing refresh: {e}")
            return Response(f"Error forcing refresh: {e}", status=500)

        
    def run(self):
        """Start the server"""
        logger.info(f"Starting KPTV FAST Streams on port {self.port}")
        logger.info(f"Enabled providers: {list(self.providers.keys())}")
        logger.info(f"Performance: {self.max_workers} workers, {self.provider_timeout}s timeout")
        if self.git_country:
            logger.info(f"Git country filter: {self.git_country}")
        
        try:
            server = WSGIServer(('0.0.0.0', self.port), self.app, log=None)
            server.serve_forever()
        except Exception as e:
            logger.error(f"Server error: {e}")
            raise

if __name__ == '__main__':
    try:
        app = UnifiedStreamingAggregator()
        app.run()
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        exit(1)