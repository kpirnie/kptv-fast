"""
EPG Aggregator - Fetches and combines EPG sources into a single XMLTV file
"""

import requests
import gzip
import xml.etree.ElementTree as ET
import time
import threading
import logging
from typing import Dict, List, Optional, Set
from io import BytesIO
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class EPGAggregator:
    """Aggregates EPG data from multiple external sources into a single XMLTV"""
    
    def __init__(self):
        self.cache = None
        self.cache_expiry = 0
        self.cache_lock = threading.Lock()
        self.cache_duration = 3600  # 1 hour
        
        # All available EPG sources
        self.epg_sources = {
            # mjh.nz sources
            'plex': 'https://i.mjh.nz/Plex/all.xml',
            'pluto': 'https://i.mjh.nz/PlutoTV/all.xml',
            'samsung': 'https://i.mjh.nz/SamsungTVPlus/all.xml',
            'stirr': 'https://i.mjh.nz/Stirr/all.xml',
            
            # Additional sources
            'tubi': 'https://raw.githubusercontent.com/BuddyChewChew/tubi-scraper/main/tubi_epg.xml',
            'xumo': 'https://raw.githubusercontent.com/BuddyChewChew/xumo-playlist-generator/main/playlists/xumo_epg.xml',
            
            # epgshare01 sources (gzipped)
            'plex_epgshare': 'https://epgshare01.online/epgshare01/epg_ripper_PLEX1.xml.gz',
            'distrotv_epgshare': 'https://epgshare01.online/epgshare01/epg_ripper_DISTROTV1.xml.gz',
        }
        
        # Sources to use by default (can be configured)
        self.enabled_sources = [
            'plex', 'pluto', 'samsung', 'stirr', 'tubi', 'xumo'
        ]
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            'Accept': 'application/xml, text/xml, */*',
        })
        self.session.max_redirects = 5
    
    def get_combined_epg(self, force_refresh: bool = False) -> str:
        """Get combined EPG XML from all sources"""
        with self.cache_lock:
            if not force_refresh and self.cache and time.time() < self.cache_expiry:
                logger.debug("Returning cached combined EPG")
                return self.cache
        
        logger.info("Building combined EPG from all sources...")
        start_time = time.time()
        
        # Create root XMLTV element
        root = ET.Element('tv')
        root.set('generator-info-name', 'KPTV-FAST EPG Aggregator')
        root.set('generated-ts', datetime.now(timezone.utc).isoformat())
        
        channels_added: Set[str] = set()
        programmes_count = 0
        successful_sources = []
        failed_sources = []
        
        # Fetch each source in parallel
        import concurrent.futures
        
        def fetch_source(source_name: str) -> tuple:
            """Fetch and parse a single EPG source"""
            url = self.epg_sources.get(source_name)
            if not url:
                return source_name, None, None, f"Unknown source: {source_name}"
            
            try:
                response = self.session.get(url, timeout=(10, 120), allow_redirects=True)
                response.raise_for_status()
                
                content = response.content
                
                # Handle gzipped content
                if url.endswith('.gz') or response.headers.get('Content-Encoding') == 'gzip':
                    try:
                        content = gzip.decompress(content)
                    except gzip.BadGzipFile:
                        pass  # Not actually gzipped
                
                xml_content = content.decode('utf-8')
                
                # Parse XML
                source_root = ET.fromstring(xml_content)
                
                channels = source_root.findall('channel')
                programmes = source_root.findall('programme')
                
                return source_name, channels, programmes, None
                
            except requests.Timeout:
                return source_name, None, None, "Timeout"
            except requests.RequestException as e:
                return source_name, None, None, f"Request error: {e}"
            except ET.ParseError as e:
                return source_name, None, None, f"XML parse error: {e}"
            except Exception as e:
                return source_name, None, None, f"Error: {e}"
        
        # Fetch all sources concurrently
        with concurrent.futures.ThreadPoolExecutor(max_workers=6) as executor:
            futures = {
                executor.submit(fetch_source, source): source 
                for source in self.enabled_sources
            }
            
            for future in concurrent.futures.as_completed(futures):
                source_name, channels, programmes, error = future.result()
                
                if error:
                    logger.warning(f"EPG source {source_name} failed: {error}")
                    failed_sources.append(source_name)
                    continue
                
                if channels is None or programmes is None:
                    failed_sources.append(source_name)
                    continue
                
                # Add channels (avoiding duplicates by channel id)
                channels_from_source = 0
                for channel in channels:
                    channel_id = channel.get('id')
                    if channel_id and channel_id not in channels_added:
                        root.append(channel)
                        channels_added.add(channel_id)
                        channels_from_source += 1
                
                # Add all programmes
                programmes_from_source = 0
                for programme in programmes:
                    root.append(programme)
                    programmes_from_source += 1
                
                programmes_count += programmes_from_source
                successful_sources.append(source_name)
                
                logger.info(f"EPG source {source_name}: {channels_from_source} channels, {programmes_from_source} programmes")
        
        # Generate XML string
        xml_declaration = '<?xml version="1.0" encoding="UTF-8"?>\n'
        xml_doctype = '<!DOCTYPE tv SYSTEM "xmltv.dtd">\n'
        xml_content = ET.tostring(root, encoding='unicode')
        
        combined_xml = xml_declaration + xml_doctype + xml_content
        
        elapsed = time.time() - start_time
        logger.info(f"Combined EPG built: {len(channels_added)} channels, {programmes_count} programmes from {len(successful_sources)} sources in {elapsed:.1f}s")
        
        if failed_sources:
            logger.warning(f"Failed EPG sources: {', '.join(failed_sources)}")
        
        # Cache the result
        with self.cache_lock:
            self.cache = combined_xml
            self.cache_expiry = time.time() + self.cache_duration
        
        return combined_xml
    
    def get_combined_epg_gzipped(self, force_refresh: bool = False) -> bytes:
        """Get combined EPG as gzipped bytes"""
        xml_content = self.get_combined_epg(force_refresh)
        return gzip.compress(xml_content.encode('utf-8'))
    
    def set_enabled_sources(self, sources: List[str]):
        """Set which EPG sources to use"""
        valid_sources = [s for s in sources if s in self.epg_sources]
        if valid_sources:
            self.enabled_sources = valid_sources
            # Invalidate cache
            with self.cache_lock:
                self.cache = None
                self.cache_expiry = 0
            logger.info(f"EPG sources set to: {', '.join(valid_sources)}")
    
    def add_custom_source(self, name: str, url: str):
        """Add a custom EPG source"""
        self.epg_sources[name] = url
        logger.info(f"Added custom EPG source: {name} -> {url}")
    
    def get_available_sources(self) -> Dict[str, str]:
        """Get all available EPG sources"""
        return self.epg_sources.copy()
    
    def get_enabled_sources(self) -> List[str]:
        """Get currently enabled EPG sources"""
        return self.enabled_sources.copy()
    
    def clear_cache(self):
        """Clear the EPG cache"""
        with self.cache_lock:
            self.cache = None
            self.cache_expiry = 0
        logger.info("EPG cache cleared")


# Singleton instance
_aggregator_instance = None
_instance_lock = threading.Lock()


def get_epg_aggregator() -> EPGAggregator:
    """Get the singleton EPG aggregator instance"""
    global _aggregator_instance
    with _instance_lock:
        if _aggregator_instance is None:
            _aggregator_instance = EPGAggregator()
        return _aggregator_instance