"""
EPG Aggregator - Downloads and combines EPG sources into single XMLTV
"""

import requests
import gzip
import time
import threading
import logging
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


class EPGAggregator:
    """Downloads and combines external EPG sources into single XMLTV"""
    
    def __init__(self):
        self.cache = None
        self.cache_gz = None
        self.cache_expiry = 0
        self.cache_lock = threading.Lock()
        self.cache_duration = 3600  # 1 hour
        
        # EPG sources to fetch and combine
        self.epg_sources = {
            'plex': 'https://i.mjh.nz/Plex/all.xml',
            'pluto': 'https://i.mjh.nz/PlutoTV/all.xml',
            'samsung': 'https://i.mjh.nz/SamsungTVPlus/all.xml',
            'stirr': 'https://i.mjh.nz/Stirr/all.xml',
            'lg': 'https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz',
            'distrotv': 'https://epgshare01.online/epgshare01/epg_ripper_DISTROTV1.xml.gz',
            'tubi': 'https://github.com/BuddyChewChew/tubi-scraper/raw/refs/heads/main/tubi_epg.xml',
            'xumo': 'https://raw.githubusercontent.com/BuddyChewChew/xumo-playlist-generator/main/playlists/xumo_epg.xml.gz',
        }
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
            'Accept': '*/*',
        })
    
    def _fetch_source(self, name: str, url: str) -> str:
        """Fetch single EPG source, return raw XML content"""
        try:
            response = self.session.get(url, timeout=(10, 120))
            response.raise_for_status()
            
            content = response.content
            
            # Decompress if gzipped
            if url.endswith('.gz'):
                try:
                    content = gzip.decompress(content)
                except:
                    pass
            
            xml_text = content.decode('utf-8')
            logger.info(f"Fetched EPG: {name} ({len(xml_text)} bytes)")
            return xml_text
            
        except Exception as e:
            logger.error(f"Failed to fetch EPG {name}: {e}")
            return ""
    
    def _extract_content(self, xml_text: str) -> tuple:
        """Extract channels and programmes from XMLTV, return as raw strings"""
        channels = []
        programmes = []
        
        try:
            import re
            
            channel_pattern = re.compile(r'<channel\s[^>]*>.*?</channel>', re.DOTALL)
            programme_pattern = re.compile(r'<programme\s[^>]*>.*?</programme>', re.DOTALL)
            
            channels = channel_pattern.findall(xml_text)
            programmes = programme_pattern.findall(xml_text)
            
        except Exception as e:
            logger.error(f"Error extracting content: {e}")
        
        return channels, programmes
    
    def get_combined_epg(self, force_refresh: bool = False) -> str:
        """Get combined EPG XML from all sources"""
        
        with self.cache_lock:
            if not force_refresh and self.cache and time.time() < self.cache_expiry:
                logger.debug("Returning cached EPG")
                return self.cache
        
        logger.info("Building combined EPG...")
        start_time = time.time()
        
        all_channels = []
        all_programmes = []
        seen_channel_ids = set()
        
        # Fetch all sources
        for name, url in self.epg_sources.items():
            xml_text = self._fetch_source(name, url)
            if not xml_text:
                continue
            
            channels, programmes = self._extract_content(xml_text)
            
            # Add channels (dedupe by id)
            import re
            for ch in channels:
                id_match = re.search(r'id="([^"]+)"', ch)
                if id_match:
                    ch_id = id_match.group(1)
                    if ch_id not in seen_channel_ids:
                        seen_channel_ids.add(ch_id)
                        all_channels.append(ch)
            
            # Add all programmes
            all_programmes.extend(programmes)
            
            logger.info(f"  {name}: {len(channels)} channels, {len(programmes)} programmes")
        
        # Build combined XML
        xml_parts = [
            '<?xml version="1.0" encoding="UTF-8"?>',
            '<!DOCTYPE tv SYSTEM "xmltv.dtd">',
            f'<tv generator-info-name="KPTV-FAST" generated-ts="{datetime.now(timezone.utc).isoformat()}">',
        ]
        
        xml_parts.extend(all_channels)
        xml_parts.extend(all_programmes)
        xml_parts.append('</tv>')
        
        combined_xml = '\n'.join(xml_parts)
        
        elapsed = time.time() - start_time
        logger.info(f"Combined EPG: {len(all_channels)} channels, {len(all_programmes)} programmes in {elapsed:.1f}s")
        
        # Cache results
        with self.cache_lock:
            self.cache = combined_xml
            self.cache_gz = gzip.compress(combined_xml.encode('utf-8'))
            self.cache_expiry = time.time() + self.cache_duration
        
        return combined_xml
    
    def get_combined_epg_gzipped(self, force_refresh: bool = False) -> bytes:
        """Get combined EPG as gzipped bytes"""
        with self.cache_lock:
            if not force_refresh and self.cache_gz and time.time() < self.cache_expiry:
                return self.cache_gz
        
        self.get_combined_epg(force_refresh)
        
        with self.cache_lock:
            return self.cache_gz
    
    def clear_cache(self):
        """Clear the EPG cache"""
        with self.cache_lock:
            self.cache = None
            self.cache_gz = None
            self.cache_expiry = 0
        logger.info("EPG cache cleared")


# Singleton
_instance = None
_lock = threading.Lock()


def get_epg_aggregator() -> EPGAggregator:
    """Get singleton EPG aggregator"""
    global _instance
    with _lock:
        if _instance is None:
            _instance = EPGAggregator()
        return _instance