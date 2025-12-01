"""
Samsung TV Plus Provider Implementation
"""

import requests
import json
import uuid
import gzip
import os
import time
import string
import random
from datetime import datetime, timedelta
from typing import List, Dict, Any
from io import BytesIO
from .base_provider import BaseProvider

class SamsungProvider(BaseProvider):
    """Provider for Samsung TV Plus channels"""
    
    def __init__(self):
        super().__init__("samsung")
        
        # Get region from environment or default to US
        self.region = os.getenv('SAMSUNG_REGION', 'us')
        
        self.app_url = 'https://i.mjh.nz/SamsungTVPlus/.channels.json.gz'
        self.playback_url_template = 'https://jmp2.uk/sam-{id}.m3u8'
        
        self.headers = {
            'User-Agent': self.get_user_agent(),
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    
    def _get_app_data(self) -> dict:
        """Get Samsung TV Plus app data"""
        try:
            response = requests.get(self.app_url, headers=self.headers, timeout=self.get_timeout())
            response.raise_for_status()
            
            # Decompress and parse JSON
            json_text = gzip.GzipFile(fileobj=BytesIO(response.content)).read()
            data = json.loads(json_text)
            
            return data.get('regions', {})
            
        except Exception as e:
            self.logger.error(f"Error getting Samsung app data: {e}")
            return {}
    
    def get_channels(self) -> List[Dict[str, Any]]:
        """Get Samsung TV Plus channels"""
        try:
            all_regions = self._get_app_data()
            
            if not all_regions:
                self.logger.error("Could not get Samsung channel data")
                return []
            
            # Use specified region or all regions
            if self.region == 'all':
                regions_to_process = all_regions.values()
                self.logger.info("Processing Samsung channels from all regions")
            elif self.region in all_regions:
                regions_to_process = [all_regions[self.region]]
                self.logger.info(f"Processing Samsung channels from region: {self.region}")
            else:
                self.logger.warning(f"Region {self.region} not found in Samsung data, using US")
                regions_to_process = [all_regions.get('us', {})]
            
            processed_channels = []
            
            for region_data in regions_to_process:
                channels = region_data.get('channels', {})
                region_name = region_data.get('name', 'Unknown')
                
                for channel_id, channel_data in channels.items():
                    try:
                        name = channel_data.get('name', '')
                        logo = channel_data.get('logo', '')
                        group = channel_data.get('group', 'General')
                        chno = channel_data.get('chno')
                        
                        if not name:
                            continue
                        
                        # Skip channels that require a license (DRM)
                        if channel_data.get('license_url'):
                            self.logger.debug(f"Skipping DRM channel: {name}")
                            continue
                        
                        # Build stream URL
                        stream_url = self.playback_url_template.format(id=channel_id)
                        
                        channel = {
                            'id': f"samsung-{channel_id}",
                            'name': name,
                            'stream_url': stream_url,
                            'logo': logo,
                            'group': group,
                            'number': int(chno) if chno else None,
                            'description': f"Samsung TV Plus channel: {name}",
                            'language': 'en'
                        }
                        
                        if self.validate_channel(channel):
                            processed_channels.append(self.normalize_channel(channel))
                            
                    except Exception as e:
                        self.logger.warning(f"Error processing Samsung channel: {e}")
                        continue
            
            self.logger.info(f"Successfully processed {len(processed_channels)} Samsung TV Plus channels")
            return processed_channels
            
        except Exception as e:
            self.logger.error(f"Error fetching Samsung TV Plus channels: {e}")
            return []
    
    def get_epg_data(self):
        """EPG handled by aggregator"""
        return {}