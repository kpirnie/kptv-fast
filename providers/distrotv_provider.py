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
    
    def get_epg_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get EPG data for DistroTV channels"""
        try:
            feed = self._load_feed()
            
            if not feed.get("shows"):
                return {}
            
            # Build mapping of episode IDs to channel names
            ids = {}
            for ch in feed["shows"].values():
                try:
                    seasons = ch.get("seasons", [])
                    if not seasons:
                        continue
                    
                    episodes = seasons[0].get("episodes", [])
                    if not episodes:
                        continue
                    
                    episode_id = str(episodes[0].get("id", ""))
                    channel_name = ch.get("name", "")
                    
                    if episode_id and channel_name:
                        ids[episode_id] = channel_name
                        
                except Exception as e:
                    self.logger.debug(f"Error building EPG ID mapping: {e}")
                    continue
            
            if not ids:
                self.logger.warning("No EPG IDs found for DistroTV")
                return {}
            
            # Fetch EPG data
            epg_url = f"{self.epg_url}?id={','.join(ids.keys())}"
            response = self.make_request('GET', epg_url, headers=self.headers)
            response.raise_for_status()
            data = response.json()
            
            epg_data = {}
            epg_response = data.get("epg", {})
            
            for episode_id, channel_name in ids.items():
                ch_epg = epg_response.get(episode_id)
                if ch_epg is None:
                    continue
                
                slots = ch_epg.get("slots", [])
                if not slots:
                    continue
                
                our_channel_id = f"distrotv-{channel_name}"
                programmes = []
                
                for slot in slots:
                    try:
                        title = slot.get("title", "").strip()
                        if not title:
                            continue
                        
                        start_str = slot.get("start", "")
                        end_str = slot.get("end", "")
                        
                        if not start_str or not end_str:
                            continue
                        
                        # Parse datetime strings
                        start_dt = datetime.strptime(start_str, '%Y-%m-%d %H:%M:%S')
                        end_dt = datetime.strptime(end_str, '%Y-%m-%d %H:%M:%S')
                        
                        programme = {
                            'title': title,
                            'description': (slot.get("description") or "").strip(),
                            'start': start_dt.strftime('%Y%m%d%H%M%S') + " +0000",
                            'stop': end_dt.strftime('%Y%m%d%H%M%S') + " +0000",
                        }
                        
                        if self.validate_programme(programme):
                            programmes.append(self.normalize_programme(programme))
                            
                    except Exception as e:
                        self.logger.debug(f"Error processing DistroTV EPG slot: {e}")
                        continue
                
                if programmes:
                    epg_data[our_channel_id] = programmes
            
            self.logger.info(f"Retrieved EPG data for {len(epg_data)} DistroTV channels")
            return epg_data
            
        except Exception as e:
            self.logger.error(f"Error fetching DistroTV EPG data: {e}")
            return {}