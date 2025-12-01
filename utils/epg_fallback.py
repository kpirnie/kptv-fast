"""
EPG Fallback System for External Sources
"""

import requests
import gzip
import xml.etree.ElementTree as ET
import time
import threading
import re
from typing import Dict, List, Any, Optional
from io import BytesIO
import logging

logger = logging.getLogger(__name__)

class EPGFallbackManager:
    """Manages fallback EPG sources"""
    
    def __init__(self):
        self.cache = {}
        self.cache_expiry = {}
        self.cache_lock = threading.Lock()
        self.cache_duration = 3600  # 1 hour
        
        self.fallback_sources = {
            'epgshare01': {
                'plex': 'https://epgshare01.online/epgshare01/epg_ripper_PLEX1.xml.gz',
                'lg': 'https://epgshare01.online/epgshare01/epg_ripper_US2.xml.gz',
                'distrotv': 'https://epgshare01.online/epgshare01/epg_ripper_DISTROTV1.xml.gz',
            },
            'mjh': {
                'pluto': 'https://i.mjh.nz/PlutoTV/all.xml.gz',
                'plex': 'https://i.mjh.nz/Plex/all.xml.gz',
                'samsung': 'https://i.mjh.nz/SamsungTVPlus/all.xml.gz',
                'distrotv': 'https://i.mjh.nz/DStv/za.xml.gz',
                'stirr': 'https://i.mjh.nz/Stirr/all.xml.gz', 
            },
            'buddychewchew': {
                'tubi': 'https://raw.githubusercontent.com/BuddyChewChew/tubi-scraper/main/tubi_epg.xml',
                'xumo': 'https://raw.githubusercontent.com/BuddyChewChew/xumo-playlist-generator/main/playlists/xumo_epg.xml.gz',
            },
        }
        
        self.session = requests.Session()
        self.session.headers.update({
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
        })
        self.session.max_redirects = 5
    
    def get_fallback_epg(self, provider_name: str, channels: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
        """Get EPG from fallback sources"""
        channel_ids = {ch.get('id', '') for ch in channels if ch.get('id')}
        
        # Try sources in order of preference
        for source_name in ['mjh', 'buddychewchew', 'epgshare01']:
            if provider_name in self.fallback_sources[source_name]:
                epg_data = self._fetch_source_epg(source_name, provider_name)
                if epg_data:
                    # Filter to our channels
                    filtered_epg = {}
                    for channel_id, programmes in epg_data.items():
                        if channel_id in channel_ids:
                            filtered_epg[channel_id] = programmes
                    
                    if filtered_epg:
                        logger.info(f"Fallback EPG from {source_name} for {provider_name}: {len(filtered_epg)} channels")
                        return filtered_epg
        
        return {}
    
    def _fetch_source_epg(self, source_name: str, provider_name: str) -> Dict[str, List[Dict[str, Any]]]:
        """Fetch EPG from specific source"""
        cache_key = f"{source_name}_{provider_name}"
        
        with self.cache_lock:
            if (cache_key in self.cache and 
                cache_key in self.cache_expiry and 
                time.time() < self.cache_expiry[cache_key]):
                return self.cache[cache_key]
        
        url = self.fallback_sources[source_name][provider_name]
        
        try:
            response = self.session.get(url, timeout=(10, 60), allow_redirects=True)
            response.raise_for_status()
            
            # Handle compression - check content or URL
            content = response.content
            if url.endswith('.gz') or response.headers.get('Content-Encoding') == 'gzip':
                try:
                    xml_content = gzip.decompress(content).decode('utf-8')
                except gzip.BadGzipFile:
                    # Not actually gzipped despite extension
                    xml_content = content.decode('utf-8')
            else:
                xml_content = content.decode('utf-8')
            
            epg_data = self._parse_xmltv(xml_content, provider_name)
            
            with self.cache_lock:
                self.cache[cache_key] = epg_data
                self.cache_expiry[cache_key] = time.time() + self.cache_duration
            
            return epg_data
            
        except Exception as e:
            logger.warning(f"Failed to fetch {source_name} EPG for {provider_name}: {e}")
            return {}
    
    def _parse_xmltv(self, xml_content: str, provider_name: str) -> Dict[str, List[Dict[str, Any]]]:
        """Parse XMLTV format"""
        epg_data = {}
        
        try:
            root = ET.fromstring(xml_content)
            
            for programme in root.findall('programme'):
                channel_id = programme.get('channel', '')
                if not channel_id:
                    continue
                
                # Map channel ID to our format
                mapped_id = self._map_channel_id(channel_id, provider_name)
                if not mapped_id:
                    continue
                
                title_elem = programme.find('title')
                desc_elem = programme.find('desc')
                
                if title_elem is not None and title_elem.text:
                    programme_info = {
                        'title': title_elem.text.strip(),
                        'description': desc_elem.text.strip() if desc_elem is not None and desc_elem.text else '',
                        'start': programme.get('start', ''),
                        'stop': programme.get('stop', ''),
                    }
                    
                    if mapped_id not in epg_data:
                        epg_data[mapped_id] = []
                    epg_data[mapped_id].append(programme_info)
            
            return epg_data
            
        except Exception as e:
            logger.error(f"Error parsing XMLTV for {provider_name}: {e}")
            return {}
    
    def _map_channel_id(self, external_id: str, provider_name: str) -> Optional[str]:
        """Map external channel ID to internal format"""
        if provider_name == 'pluto':
            return f"pluto-{external_id}" if not external_id.startswith('pluto-') else external_id
        elif provider_name == 'plex':
            # mjh.nz uses format: lineupId-channelId (e.g., 5e20b730f2f8d5003d739db7-61e805952502a7a6fa84d70f)
            # Our provider uses: plex-channelId (e.g., plex-61e805952502a7a6fa84d70f)
            if '-' in external_id and not external_id.startswith('plex-'):
                # Extract the channel part (after the first dash)
                parts = external_id.split('-', 1)
                if len(parts) == 2:
                    channel_part = parts[1]
                    return f"plex-{channel_part}"
            return f"plex-{external_id}" if not external_id.startswith('plex-') else external_id
        elif provider_name == 'tubi':
            return f"tubi-{external_id}" if not external_id.startswith('tubi-') else external_id
        elif provider_name == 'xumo':
            return external_id if not external_id.startswith('xumo-') else external_id
        elif provider_name == 'samsung':
            return f"samsung-{external_id}" if not external_id.startswith('samsung-') else external_id
        elif provider_name == 'distrotv':
            return f"distrotv-{external_id}" if not external_id.startswith('distrotv-') else external_id
        elif provider_name == 'lg':
            return f"lg-{external_id}" if not external_id.startswith('lg-') else external_id
        elif provider_name == 'stirr':
            return f"stirr-{external_id}" if not external_id.startswith('stirr-') else external_id
        
        return external_id