"""
Pluto Provider Implementation
"""

import requests
import json
import uuid
import os
from datetime import datetime
from typing import List, Dict, Any
from .base_provider import BaseProvider

class PlutoProvider(BaseProvider):
    """Provider for Pluto TV channels"""
    
    def __init__(self):
        super().__init__("pluto")
        
        self.device_id = str(uuid.uuid1())
        self.session_token = None
        self.session_expires_at = 0
        
        # Get region from environment or default to US West
        self.region = os.getenv('PLUTO_REGION', 'us_west')
        
        # Regional IP addresses for geo-spoofing
        self.x_forward = {
            "local": "",
            "uk": "178.238.11.6",
            "ca": "192.206.151.131", 
            "fr": "193.169.64.141",
            "us_east": "108.82.206.181",
            "us_west": "76.81.9.69",
        }
        
        self.headers = {
            'authority': 'boot.pluto.tv',
            'accept': '*/*',
            'accept-language': 'en-US,en;q=0.9',
            'origin': 'https://pluto.tv',
            'referer': 'https://pluto.tv/',
            'user-agent': self.get_user_agent(),
        }
        
        if self.region in self.x_forward:
            forwarded_ip = self.x_forward[self.region]
            if forwarded_ip:
                self.headers["X-Forwarded-For"] = forwarded_ip
    
    def _get_session_token(self) -> str:
        """Get or refresh session token"""
        if self.session_token and datetime.now().timestamp() < self.session_expires_at:
            return self.session_token
        
        try:
            url = 'https://boot.pluto.tv/v4/start'
            params = {
                'appName': 'web',
                'appVersion': '8.0.0-111b2b9dc00bd0bea9030b30662159ed9e7c8bc6',
                'deviceVersion': '122.0.0',
                'deviceModel': 'web',
                'deviceMake': 'chrome',
                'deviceType': 'web',
                'clientID': str(uuid.uuid4()),
                'clientModelNumber': '1.0.0',
                'serverSideAds': 'false',
                'drmCapabilities': 'widevine:L3',
                'blockingMode': '',
                'notificationVersion': '1',
                'appLaunchCount': '',
                'lastAppLaunchDate': '',
            }
            
            response = requests.get(url, headers=self.headers, params=params, timeout=self.get_timeout())
            response.raise_for_status()
            
            data = response.json()
            self.session_token = data.get('sessionToken')
            
            if not self.session_token:
                self.logger.error("No session token received from Pluto")
                return ""
            
            # Set expiry for 4 hours from now
            self.session_expires_at = datetime.now().timestamp() + (4 * 3600)
            self.logger.info(f"Got new Pluto session token for region: {self.region}")
            
            return self.session_token
            
        except Exception as e:
            self.logger.error(f"Error getting Pluto session token: {e}")
            return ""
    
    def get_channels(self) -> List[Dict[str, Any]]:
        """Get Pluto TV channels"""
        try:
            token = self._get_session_token()
            if not token:
                self.logger.error("Could not get Pluto session token")
                return []
            
            # Get channels
            url = "https://service-channels.clusters.pluto.tv/v2/guide/channels"
            headers = {
                'authority': 'service-channels.clusters.pluto.tv',
                'accept': '*/*',
                'accept-language': 'en-US,en;q=0.9',
                'authorization': f'Bearer {token}',
                'origin': 'https://pluto.tv',
                'referer': 'https://pluto.tv/',
                'user-agent': self.get_user_agent(),
            }
            
            # Add regional IP forwarding
            if self.region in self.x_forward:
                forwarded_ip = self.x_forward[self.region]
                if forwarded_ip:
                    headers["X-Forwarded-For"] = forwarded_ip
            
            params = {
                'channelIds': '',
                'offset': '0',
                'limit': '1000',
                'sort': 'number:asc',
            }
            
            response = requests.get(url, params=params, headers=headers, timeout=self.get_timeout())
            response.raise_for_status()
            
            channel_data = response.json().get("data", [])
            
            if not channel_data:
                self.logger.error("No channel data received from Pluto")
                return []
            
            # Get categories for grouping
            categories_list = self._get_categories(headers, params)
            
            # Process channels
            processed_channels = []
            
            for channel in channel_data:
                try:
                    channel_id = channel.get('id')
                    name = channel.get('name')
                    slug = channel.get('slug')
                    number = channel.get('number', 0)
                    summary = channel.get('summary', '')
                    
                    if not channel_id or not name:
                        continue
                    
                    # Find logo - look for colorLogoPNG type
                    logo = ""
                    images = channel.get('images', [])
                    for image in images:
                        if image.get('type') == 'colorLogoPNG':
                            logo = image.get('url', '')
                            break
                    
                    # Get category/group
                    group = categories_list.get(channel_id, 'General')
                    
                    # Build stream URL using the channel ID and device info
                    sid = str(uuid.uuid4())
                    stream_url = (
                        f"https://cfd-v4-service-channel-stitcher-use1-1.prd.pluto.tv/stitch/hls/channel/{channel_id}/master.m3u8"
                        f"?advertisingId=&appName=web&appVersion=unknown&appStoreUrl=&architecture=&buildVersion="
                        f"&clientTime=0&deviceDNT=0&deviceId={self.device_id}&deviceMake=Chrome&deviceModel=web"
                        f"&deviceType=web&deviceVersion=unknown&includeExtendedEvents=false&sid={sid}"
                        f"&userId=&serverSideAds=true"
                    )
                    
                    channel_info = {
                        'id': str(channel_id),
                        'name': name,
                        'stream_url': stream_url,
                        'logo': logo,
                        'group': group,
                        'number': int(number) if number else None,
                        'description': f"Pluto TV channel: {name}" + (f" - {summary}" if summary else ""),
                        'language': 'en'
                    }
                    
                    if self.validate_channel(channel_info):
                        processed_channels.append(self.normalize_channel(channel_info))
                        
                except Exception as e:
                    self.logger.warning(f"Error processing Pluto channel: {e}")
                    continue
            
            self.logger.info(f"Successfully processed {len(processed_channels)} Pluto channels from region: {self.region}")
            return processed_channels
            
        except Exception as e:
            self.logger.error(f"Error fetching Pluto channels: {e}")
            return []
    
    def _get_categories(self, headers: dict, params: dict) -> dict:
        """Get channel categories for grouping"""
        try:
            category_url = "https://service-channels.clusters.pluto.tv/v2/guide/categories"
            
            response = requests.get(category_url, params=params, headers=headers, timeout=self.get_timeout())
            response.raise_for_status()
            
            categories_data = response.json().get("data", [])
            categories_list = {}
            
            for elem in categories_data:
                category = elem.get('name', 'General')
                channel_ids = elem.get('channelIDs', [])
                for channel_id in channel_ids:
                    categories_list[channel_id] = category
            
            self.logger.info(f"Loaded {len(categories_list)} Pluto channel categories")
            return categories_list
            
        except Exception as e:
            self.logger.warning(f"Error getting Pluto categories: {e}")
            return {}
    
    def get_epg_data(self):
        """EPG handled by aggregator"""
        return {}