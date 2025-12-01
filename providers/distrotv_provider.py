"""
DistroTV Provider Implementation
Uses official DistroTV API endpoints
"""

import requests
import time
from datetime import datetime
from typing import List, Dict, Any
from .base_provider import BaseProvider


class DistroTVProvider(BaseProvider):
    """Provider for DistroTV channels"""
    
    def __init__(self):
        super().__init__("distrotv")
        
        self.feed_url = "https://tv.jsrdn.com/tv_v5/getfeed.php"
        self.epg_url = "https://tv.jsrdn.com/epg/query.php"
        
        self.headers = {
            'User-Agent': 'Dalvik/2.1.0 (Linux; U; Android 9; AFTT Build/STT9.221129.002) GTV/AFTT DistroTV/2.0.9'
        }
        
        # Cache
        self.feed_cache = None
        self.feed_cache_time = 0
        self.cache_duration = 3600 * 12  # 12 hours
    
    def _load_feed(self) -> Dict[str, Any]:
        """Load and cache the DistroTV feed"""
        if self.feed_cache is not None and time.time() - self.feed_cache_time < self.cache_duration:
            return self.feed_cache
        
        try:
            response = self.make_request('GET', self.feed_url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            self.feed_cache = {
                "topics": [t for t in data.get("topics", []) if t.get("type") == "live"],
                "shows": {k: v for k, v in data.get("shows", {}).items() if v.get("type") == "live"},
            }
            self.feed_cache_time = time.time()
            
            self.logger.info(f"Loaded DistroTV feed with {len(self.feed_cache['shows'])} live channels")
            return self.feed_cache
            
        except Exception as e:
            self.logger.error(f"Error loading DistroTV feed: {e}")
            return {"topics": [], "shows": {}}
    
    def get_channels(self) -> List[Dict[str, Any]]:
        """Get DistroTV channels"""
        try:
            feed = self._load_feed()
            
            if not feed.get("shows"):
                self.logger.warning("No DistroTV shows found in feed")
                return []
            
            channels = []
            for ch in feed["shows"].values():
                try:
                    # Extract stream URL from nested structure
                    seasons = ch.get("seasons", [])
                    if not seasons:
                        continue
                    
                    episodes = seasons[0].get("episodes", [])
                    if not episodes:
                        continue
                    
                    content = episodes[0].get("content", {})
                    stream_url = content.get("url", "")
                    
                    if not stream_url:
                        continue
                    
                    # Clean the URL (remove query params)
                    stream_url = stream_url.split('?', 1)[0]
                    
                    channel_name = ch.get("name", "")
                    title = ch.get("title", "").strip()
                    
                    if not channel_name or not title:
                        continue
                    
                    # Build genre from genre + keywords
                    genre = ch.get("genre", "")
                    keywords = ch.get("keywords", "")
                    group = genre if genre else "DistroTV"
                    
                    channel = {
                        'id': f"distrotv-{channel_name}",
                        'name': title,
                        'stream_url': stream_url,
                        'logo': ch.get("img_logo", ""),
                        'group': group,
                        'description': ch.get("description", "").strip(),
                        'language': 'en'
                    }
                    
                    if self.validate_channel(channel):
                        channels.append(self.normalize_channel(channel))
                        
                except Exception as e:
                    self.logger.debug(f"Error processing DistroTV channel: {e}")
                    continue
            
            self.logger.info(f"Successfully processed {len(channels)} DistroTV channels")
            return channels
            
        except Exception as e:
            self.logger.error(f"Error fetching DistroTV channels: {e}")
            return []
    
    def get_epg_data(self):
        """EPG handled by aggregator"""
        return {}