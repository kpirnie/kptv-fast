"""
Xumo Provider Implementation - Optimized for Speed
"""

import requests
import json
import os
import uuid
import time
import concurrent.futures
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any
from .base_provider import BaseProvider

class XumoProvider(BaseProvider):
    """Provider for Xumo TV channels - Optimized"""
    
    def __init__(self):
        super().__init__("xumo")
        
        # Configuration
        self.valencia_api_endpoint = "https://valencia-app-mds.xumo.com/v2"
        self.android_tv_endpoint = "https://android-tv-mds.xumo.com/v2"
        self.geo_id = "us"
        self.primary_list_id = "10006"
        
        # Create session for connection reuse
        self.session = requests.Session()
        
        # Headers
        self.web_headers = {
            'User-Agent': self.get_user_agent(),
            'Accept': 'application/json, text/plain, */*',
            'Accept-Language': 'en-US,en;q=0.9',
            'Origin': 'https://play.xumo.com',
            'Referer': 'https://play.xumo.com/',
        }
        
        self.android_tv_headers = {
            'User-Agent': 'okhttp/4.9.3',
        }
        
        # Cache for stream URLs to avoid repeated lookups
        self.stream_cache = {}
        
    def _fetch_data(self, url: str, headers: dict = None, params: dict = None, retries: int = 1) -> dict:
        """Fetch data from URL with retries - optimized"""
        if headers is None:
            headers = self.web_headers
            
        for attempt in range(retries + 1):
            try:
                response = self.session.get(
                    url, 
                    headers=headers, 
                    params=params, 
                    timeout=(5, 15)  # Shorter timeout: 5s connect, 15s read
                )
                response.raise_for_status()
                
                if response.content:
                    return response.json()
                else:
                    self.logger.debug(f"Empty response from {url}")
                    return {}
                    
            except requests.exceptions.RequestException as e:
                self.logger.debug(f"Attempt {attempt + 1} failed for {url}: {e}")
                if attempt == retries:
                    self.logger.warning(f"All attempts failed for {url}")
                    return {}
                time.sleep(0.5)  # Short delay between retries
                
        return {}
    
    def _process_stream_uri(self, uri: str) -> str:
        """Process stream URI by replacing placeholders - optimized"""
        if not uri:
            return ""
            
        try:
            # Pre-generate values once
            timestamp = str(int(time.time() * 1000))
            device_uuid = str(uuid.uuid4())
            session_uuid = str(uuid.uuid4())
            
            # Replace placeholders in one pass
            replacements = {
                '[PLATFORM]': "web",
                '[APP_VERSION]': "1.0.0",
                '[timestamp]': timestamp,
                '[app_bundle]': "web.xumo.com",
                '[device_make]': "UnifiedAggregator",
                '[device_model]': "WebClient",
                '[content_language]': "en",
                '[IS_LAT]': "0",
                '[IFA]': device_uuid,
                '[SESSION_ID]': session_uuid,
                '[DEVICE_ID]': device_uuid.replace('-', '')
            }
            
            for placeholder, value in replacements.items():
                uri = uri.replace(placeholder, value)
                
            # Remove any remaining placeholders
            import re
            uri = re.sub(r'\[([^]]+)\]', '', uri)
            
            return uri
            
        except Exception as e:
            self.logger.debug(f"Error processing stream URI: {e}")
            return uri
    
    def get_channels(self) -> List[Dict[str, Any]]:
        """Get Xumo channels - optimized version"""
        try:
            self.logger.debug("Starting Xumo channel fetch")
            start_time = time.time()
            
            # Get channel list from Valencia endpoint
            url = f"{self.valencia_api_endpoint}/proxy/channels/list/{self.primary_list_id}.json"
            params = {'geoId': self.geo_id}
            
            data = self._fetch_data(url, self.web_headers, params)
            if not data:
                self.logger.error("Failed to fetch Xumo channel list")
                return []
            
            # Extract channels
            channel_items = []
            if 'channel' in data and 'item' in data['channel']:
                channel_items = data['channel']['item']
            elif 'items' in data:
                channel_items = data['items']
            else:
                self.logger.error("Could not find channel list in Xumo response")
                return []
            
            self.logger.debug(f"Found {len(channel_items)} Xumo channel items")
            
            # Process channels concurrently but with limits
            processed_channels = []
            
            # Filter out DRM and non-live channels first
            valid_items = []
            for item in channel_items:
                callsign = item.get('callsign', '')
                properties = item.get('properties', {})
                is_live = properties.get('is_live') == "true"
                is_drm = callsign.endswith("-DRM") or callsign.endswith("DRM-CMS")
                
                if not is_drm and is_live:
                    valid_items.append(item)
            
            self.logger.debug(f"Filtered to {len(valid_items)} valid Xumo channels")
            
            # Process valid channels with threading for stream URL fetching
            def process_channel_item(item):
                try:
                    channel_id = item.get('guid', {}).get('value')
                    title = item.get('title')
                    number_str = item.get('number')
                    logo_url = item.get('images', {}).get('logo') or item.get('logo')
                    
                    if not channel_id or not title:
                        return None
                    
                    # Process logo URL
                    if logo_url:
                        if logo_url.startswith('//'):
                            logo_url = 'https:' + logo_url
                        elif logo_url.startswith('/'):
                            logo_url = 'https://image.xumo.com' + logo_url
                    else:
                        logo_url = f"https://image.xumo.com/v1/channels/channel/{channel_id}/168x168.png?type=color_onBlack"
                    
                    # Get genre
                    genre_list = item.get('genre')
                    genre = 'General'
                    if isinstance(genre_list, list) and genre_list:
                        if isinstance(genre_list[0], dict):
                            genre = genre_list[0].get('value', 'General')
                    elif isinstance(genre_list, str):
                        genre = genre_list
                    
                    # Get stream URL (optimized)
                    stream_url = self._get_stream_url_fast(channel_id)
                    if not stream_url:
                        self.logger.debug(f"No stream URL found for channel {channel_id}")
                        return None
                    
                    channel = {
                        'id': str(channel_id),
                        'name': title,
                        'stream_url': stream_url,
                        'logo': logo_url,
                        'group': genre,
                        'number': int(number_str) if number_str else None,
                        'description': f"Xumo channel: {title}",
                        'language': 'en'
                    }
                    
                    if self.validate_channel(channel):
                        return self.normalize_channel(channel)
                    return None
                        
                except Exception as e:
                    self.logger.debug(f"Error processing Xumo channel item: {e}")
                    return None
            
            # Use ThreadPoolExecutor for concurrent processing, but limit workers
            max_workers = min(10, len(valid_items))  # Limit concurrent requests
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                # Submit all tasks
                futures = [executor.submit(process_channel_item, item) for item in valid_items]
                
                # Collect results with timeout
                for future in concurrent.futures.as_completed(futures, timeout=30):
                    try:
                        result = future.result(timeout=5)
                        if result:
                            processed_channels.append(result)
                    except Exception as e:
                        self.logger.debug(f"Error processing Xumo channel future: {e}")
                        continue
            
            elapsed = time.time() - start_time
            self.logger.info(f"Successfully processed {len(processed_channels)} Xumo channels in {elapsed:.1f}s")
            return processed_channels
            
        except Exception as e:
            self.logger.error(f"Error fetching Xumo channels: {e}")
            return []
    
    def _get_stream_url_fast(self, channel_id: str) -> str:
        """Get stream URL for a channel - optimized version"""
        # Check cache first
        if channel_id in self.stream_cache:
            return self.stream_cache[channel_id]
        
        try:
            # Try direct stream URL pattern first (faster)
            direct_url = f"https://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv/stitch/hls/channel/{channel_id}/master.m3u8"
            
            # Quick check if this pattern works
            try:
                test_response = self.session.head(direct_url, timeout=(2, 5))
                if test_response.status_code == 200:
                    processed_url = self._process_stream_uri(direct_url)
                    self.stream_cache[channel_id] = processed_url
                    return processed_url
            except:
                pass
            
            # Fall back to API lookup (slower)
            return self._get_stream_url_api(channel_id)
            
        except Exception as e:
            self.logger.debug(f"Error getting stream URL for channel {channel_id}: {e}")
            return ""
    
    def _get_stream_url_api(self, channel_id: str) -> str:
        """Get stream URL via API lookup - fallback method"""
        try:
            # Get current broadcast
            current_hour = datetime.now(timezone.utc).hour
            broadcast_url = f"{self.android_tv_endpoint}/channels/channel/{channel_id}/broadcast.json"
            params = {'hour': current_hour}
            
            broadcast_data = self._fetch_data(broadcast_url, self.android_tv_headers, params)
            if not broadcast_data or 'assets' not in broadcast_data:
                return ""
            
            # Find current asset
            now_utc = datetime.now(timezone.utc)
            current_asset = None
            
            for asset in broadcast_data['assets']:
                start_time_str = asset.get('start')
                end_time_str = asset.get('end')
                
                if start_time_str and end_time_str:
                    try:
                        start_time = datetime.fromisoformat(start_time_str.replace('Z', '+00:00'))
                        end_time = datetime.fromisoformat(end_time_str.replace('Z', '+00:00'))
                        
                        if start_time <= now_utc < end_time:
                            current_asset = asset
                            break
                    except:
                        continue
            
            if not current_asset and broadcast_data['assets']:
                current_asset = broadcast_data['assets'][0]
            
            if not current_asset:
                return ""
            
            asset_id = current_asset.get('id')
            if not asset_id:
                return ""
            
            # Get asset details
            asset_url = f"{self.android_tv_endpoint}/assets/asset/{asset_id}.json"
            params = {'f': 'providers'}
            
            asset_data = self._fetch_data(asset_url, self.android_tv_headers, params)
            if not asset_data or 'providers' not in asset_data:
                return ""
            
            # Find stream URI
            for provider in asset_data['providers']:
                if 'sources' in provider:
                    for source in provider['sources']:
                        uri = source.get('uri')
                        if uri and (source.get('type') == 'application/x-mpegURL' or uri.endswith('.m3u8')):
                            processed_uri = self._process_stream_uri(uri)
                            self.stream_cache[channel_id] = processed_uri
                            return processed_uri
            
            return ""
            
        except Exception as e:
            self.logger.debug(f"Error getting stream URL via API for channel {channel_id}: {e}")
            return ""    

    def __del__(self):

        """Cleanup session"""
        if hasattr(self, 'session'):
            self.session.close()

    def get_epg_data(self):
        """EPG handled by aggregator"""
        return {}