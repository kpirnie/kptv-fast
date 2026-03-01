"""
Stirr Provider Implementation
Uses Thinking Media's stirr.com API with iptv-org M3U fallback
"""

import re
import time
from datetime import datetime, timezone
from typing import List, Dict, Any, Optional
from .base_provider import BaseProvider


class StirrProvider(BaseProvider):
    """Provider for Stirr channels using the Thinking Media API"""
    
    def __init__(self):
        super().__init__("stirr")
        
        # Thinking Media API endpoints
        self.base_url = "https://stirr.com/api"
        self.channels_url = f"{self.base_url}/videos/list/"
        self.playable_url = f"{self.base_url}/v2/videos"  # /{id}/playable
        self.epg_api_url = f"{self.base_url}/epg"
        
        # Fallback M3U source (iptv-org)
        self.iptv_org_stirr_url = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams/us_stirr.m3u"
        
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:145.0) Gecko/20100101 Firefox/145.0',
            'Accept': 'application/json, text/javascript, */*; q=0.01',
            'Accept-Language': 'en-US,en;q=0.5',
            'X-Requested-With': 'XMLHttpRequest',
            'Referer': 'https://stirr.com/live',
            'Content-Type': 'application/json',
        }
        
        # Cache
        self.channels_cache = None
        self.channels_cache_time = 0
        self.cache_duration = 3600  # 1 hour
    
    def _fetch_playable_url(self, video_id: int) -> Optional[str]:
        """Fetch stream URL from the playable endpoint for channels missing 'live' field"""
        try:
            url = f"{self.playable_url}/{video_id}/playable"
            response = self.make_request('POST', url, headers=self.headers)
            
            if response.status_code != 200:
                self.logger.debug(f"Playable endpoint returned {response.status_code} for video {video_id}")
                return None
            
            data = response.json()
            
            if data.get('status') != 200:
                return None
            
            # Stream URL is in data[0].media[0]
            items = data.get('data', [])
            if items and isinstance(items, list) and len(items) > 0:
                media = items[0].get('media', [])
                if media and isinstance(media, list) and len(media) > 0:
                    stream_url = media[0]
                    self.logger.debug(f"Fetched playable URL for video {video_id}: {stream_url[:50]}...")
                    return stream_url
            
            return None
            
        except Exception as e:
            self.logger.debug(f"Error fetching playable URL for video {video_id}: {e}")
            return None
    
    def _get_channels_from_api(self) -> List[Dict[str, Any]]:
        """Get channels from the Stirr API"""
        try:
            params = {
                'categories': 'all_categories',
                'content_type': '4',  # Live content
                'no_limit': 'true'
            }
            
            response = self.make_request(
                'GET', 
                self.channels_url, 
                headers=self.headers,
                params=params
            )
            response.raise_for_status()
            
            data = response.json()
            
            if data.get('status') != 200:
                self.logger.warning(f"Stirr API returned status: {data.get('status')}")
                return []
            
            # Navigate to videos.category_videos which is an array of arrays
            videos_data = data.get('videos', {})
            category_videos = videos_data.get('category_videos', [])
            
            if not category_videos:
                self.logger.warning("No category_videos found in Stirr API response")
                return []
            
            channels = []
            seen_ids = set()
            
            # Flatten the nested arrays
            for category_list in category_videos:
                if not isinstance(category_list, list):
                    continue
                    
                for video in category_list:
                    try:
                        video_id = video.get('videoid')
                        if not video_id or video_id in seen_ids:
                            continue
                        seen_ids.add(video_id)
                        
                        channel = self._parse_channel(video)
                        if channel and self.validate_channel(channel):
                            channels.append(self.normalize_channel(channel))
                            
                    except Exception as e:
                        self.logger.debug(f"Error parsing Stirr channel: {e}")
                        continue
            
            self.logger.info(f"Found {len(channels)} channels from Stirr API")
            return channels
            
        except Exception as e:
            self.logger.error(f"Error fetching Stirr channels from API: {e}")
            return []
    
    def _parse_channel(self, video: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """Parse a channel from API response"""
        video_id = video.get('videoid', '')
        title = video.get('title', '').strip()
        
        if not video_id or not title:
            return None
        
        # Get stream URL from the 'live' field - this is the complete Aniview SSAI URL
        stream_url = video.get('live', '')
        
        # If no live URL, try fetching from the playable endpoint
        if not stream_url:
            self.logger.debug(f"No 'live' URL for channel: {title}, trying playable endpoint")
            stream_url = self._fetch_playable_url(video_id)
        
        if not stream_url:
            self.logger.debug(f"No stream URL found for channel: {title}")
            return None
        
        # Get thumbnail - prefer larger sizes, also check logo field
        logo = video.get('logo', '')
        if not logo:
            thumbs = video.get('thumbs', {})
            logo = (
                thumbs.get('1280x720') or
                thumbs.get('768x432') or
                thumbs.get('632x395') or
                thumbs.get('416x260') or
                thumbs.get('original') or
                ''
            )
        
        # Get description
        description = video.get('description', '').strip()
        if len(description) > 200:
            description = description[:197] + '...'
        
        # Get category/group from categories array
        group = 'Stirr'
        categories = video.get('categories', [])
        if categories and isinstance(categories, list) and len(categories) > 0:
            cat = categories[0]
            if isinstance(cat, dict):
                group = cat.get('category_name', 'Stirr')
        
        # Get channel number if available
        channel_number = video.get('channel_number')
        
        return {
            'id': f"stirr-{video_id}",
            'name': title,
            'stream_url': stream_url,
            'logo': logo,
            'group': group,
            'number': channel_number,
            'description': description or f"Stirr channel: {title}",
            'language': 'en',
            'external_id': str(video_id),
            'epg_channel_id': video.get('epg_channel_id', ''),
            'epg_url': video.get('epg_url', '').replace('&amp;', '&'),  # Fix HTML entities
        }
    
    def _get_channels_from_m3u(self) -> List[Dict[str, Any]]:
        """Get channels from iptv-org M3U fallback"""
        try:
            self.logger.info("Using iptv-org M3U fallback for Stirr channels")
            
            response = self.make_request('GET', self.iptv_org_stirr_url, headers={
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'
            })
            response.raise_for_status()
            
            m3u_content = response.text
            channels = self._parse_m3u_content(m3u_content)
            
            if channels:
                self.logger.info(f"Parsed {len(channels)} Stirr channels from M3U fallback")
            
            return channels
            
        except Exception as e:
            self.logger.error(f"Failed to fetch Stirr fallback M3U: {e}")
            return []
    
    def _parse_m3u_content(self, content: str) -> List[Dict[str, Any]]:
        """Parse M3U playlist content"""
        channels = []
        lines = content.strip().split('\n')
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if line.startswith('#EXTINF:'):
                try:
                    url_line = ""
                    j = i + 1
                    while j < len(lines):
                        potential_url = lines[j].strip()
                        if potential_url and not potential_url.startswith('#'):
                            url_line = potential_url
                            break
                        j += 1
                    
                    if not url_line:
                        i += 1
                        continue
                    
                    extinf_content = line[8:]
                    
                    channel_name = ""
                    tvg_id = ""
                    tvg_logo = ""
                    group_title = ""
                    
                    if ',' in extinf_content:
                        attr_part, name_part = extinf_content.rsplit(',', 1)
                        channel_name = name_part.strip()
                        
                        tvg_id_match = re.search(r'tvg-id="([^"]*)"', attr_part)
                        tvg_logo_match = re.search(r'tvg-logo="([^"]*)"', attr_part)
                        group_match = re.search(r'group-title="([^"]*)"', attr_part)
                        
                        if tvg_id_match:
                            tvg_id = tvg_id_match.group(1)
                        if tvg_logo_match:
                            tvg_logo = tvg_logo_match.group(1)
                        if group_match:
                            group_title = group_match.group(1)
                    
                    if channel_name and url_line:
                        channel_id = tvg_id if tvg_id else channel_name.lower().replace(' ', '-').replace('&', 'and')
                        
                        channel = {
                            'id': f"stirr-{channel_id}",
                            'name': channel_name,
                            'stream_url': url_line,
                            'logo': tvg_logo,
                            'group': group_title or 'Stirr',
                            'description': f"Stirr channel: {channel_name}",
                            'language': 'en'
                        }
                        
                        if self.validate_channel(channel):
                            channels.append(self.normalize_channel(channel))
                    
                    i = j + 1
                    
                except Exception as e:
                    self.logger.debug(f"Error parsing M3U line: {e}")
                    i += 1
            else:
                i += 1
        
        return channels
    
    def get_channels(self) -> List[Dict[str, Any]]:
        """Get Stirr channels - try API first, then fallback to M3U"""
        try:
            # Check cache
            if self.channels_cache and time.time() - self.channels_cache_time < self.cache_duration:
                return self.channels_cache
            
            self.logger.info("Fetching Stirr channels")
            
            # Try native API first
            channels = self._get_channels_from_api()
            
            # Fallback to iptv-org M3U if API returns too few channels
            if len(channels) < 10:
                self.logger.info(f"Stirr API returned only {len(channels)} channels, using iptv-org M3U fallback")
                m3u_channels = self._get_channels_from_m3u()
                if m3u_channels:
                    # Merge, avoiding duplicates by name
                    existing_names = {c['name'].lower() for c in channels}
                    for ch in m3u_channels:
                        if ch['name'].lower() not in existing_names:
                            channels.append(ch)
            
            # Cache results
            if channels:
                self.channels_cache = channels
                self.channels_cache_time = time.time()
                self.logger.info(f"Successfully processed {len(channels)} Stirr channels")
            else:
                self.logger.warning("No Stirr channels found from any source")
            
            return channels
            
        except Exception as e:
            self.logger.error(f"Error fetching Stirr channels: {e}")
            return []

    def get_epg_data(self):
        """EPG handled by aggregator"""
        return {}
        