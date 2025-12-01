"""
LG Provider Implementation
Fetches LG channels from external M3U sources by country
"""

import requests
import re
import os
import time
from typing import List, Dict, Any, Set
from .base_provider import BaseProvider

class LGProvider(BaseProvider):
    """Provider for LG channels"""
    
    def __init__(self):
        super().__init__("lg")
        
        self.base_url = "https://www.apsattv.com"
        
        # Get country filter from environment
        self.country_filter = self._parse_country_filter()
        
        # Country code mapping for flexible filtering
        self.country_mapping = self._build_country_mapping()
        
        # Channel cache
        self.channels_cache = []
        self.cache_expiry = 0
        self.cache_duration = 3600  # 1 hour
        
        # Headers for requests
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.9',
        }
    
    def _parse_country_filter(self) -> Set[str]:
        """Parse LG_COUNTRY environment variable"""
        lg_countries = os.getenv('LG_COUNTRY', 'us').strip()
        if not lg_countries:
            return {'us'}  # Default to US
        
        countries = []
        for country in lg_countries.split(','):
            country = country.strip().lower()
            if country:
                countries.append(country)
        
        return set(countries) if countries else {'us'}
    
    def _build_country_mapping(self) -> Dict[str, List[str]]:
        """Build mapping of country codes to various representations"""
        mapping = {
            'us': ['us', 'usa', 'united states', 'america'],
            'uk': ['uk', 'gb', 'united kingdom',],
            'gb': ['gb', 'gbr', 'britain', 'great britain'],
            'ca': ['ca', 'can', 'canada'],
            'de': ['de', 'deu', 'germany', 'deutschland'],
            'fr': ['fr', 'fra', 'france'],
            'au': ['au', 'aus', 'australia'],
            'jp': ['jp', 'jpn', 'japan'],
            'kr': ['kr', 'kor', 'south korea', 'korea'],
            'in': ['in', 'ind', 'india'],
            'br': ['br', 'bra', 'brazil', 'brasil'],
            'it': ['it', 'ita', 'italy', 'italia'],
            'es': ['es', 'esp', 'spain', 'españa'],
            'mx': ['mx', 'mex', 'mexico'],
            'ar': ['ar', 'arg', 'argentina'],
            'cl': ['cl', 'chl', 'chile'],
            'co': ['co', 'col', 'colombia'],
            'pe': ['pe', 'per', 'peru'],
            'nl': ['nl', 'nld', 'netherlands', 'holland'],
            'se': ['se', 'swe', 'sweden'],
            'no': ['no', 'nor', 'norway'],
            'dk': ['dk', 'dnk', 'denmark'],
            'fi': ['fi', 'fin', 'finland'],
            'pl': ['pl', 'pol', 'poland'],
            'ru': ['ru', 'rus', 'russia'],
            'cn': ['cn', 'chn', 'china'],
            'th': ['th', 'tha', 'thailand'],
            'vn': ['vn', 'vnm', 'vietnam'],
            'id': ['id', 'idn', 'indonesia'],
            'my': ['my', 'mys', 'malaysia'],
            'sg': ['sg', 'sgp', 'singapore'],
            'ph': ['ph', 'phl', 'philippines'],
            'tw': ['tw', 'twn', 'taiwan'],
            'hk': ['hk', 'hkg', 'hong kong'],
            'za': ['za', 'zaf', 'south africa'],
            'eg': ['eg', 'egy', 'egypt'],
            'tr': ['tr', 'tur', 'turkey'],
            'ae': ['ae', 'are', 'uae', 'united arab emirates'],
            'sa': ['sa', 'sau', 'saudi arabia'],
        }
        return mapping
    
    def _get_country_codes(self) -> List[str]:
        """Get list of country codes to fetch based on filter"""
        if not self.country_filter:
            return ['us']
        
        country_codes = []
        
        for country in self.country_filter:
            # Check if it's already a 2-letter code
            if len(country) == 2 and country.isalpha():
                country_codes.append(country.lower())
            else:
                # Find matching country code
                for code, variants in self.country_mapping.items():
                    if country in variants:
                        country_codes.append(code)
                        break
        
        return list(set(country_codes)) if country_codes else ['us']
    
    def _fetch_country_m3u(self, country_code: str) -> List[Dict[str, Any]]:
        """Fetch M3U for specific country"""
        try:
            url = f"{self.base_url}/{country_code}lg.m3u"
            self.logger.debug(f"Fetching LG channels for {country_code} from {url}")
            
            response = self.make_request('GET', url, headers=self.headers)
            response.raise_for_status()
            
            m3u_content = response.text
            channels = self._parse_m3u_content(m3u_content, country_code)
            
            if channels:
                self.logger.info(f"Parsed {len(channels)} LG channels for {country_code}")
            
            return channels
            
        except Exception as e:
            self.logger.warning(f"Failed to fetch LG M3U for {country_code}: {e}")
            return []
    
    def _parse_m3u_content(self, content: str, country_code: str) -> List[Dict[str, Any]]:
        """Parse M3U playlist content"""
        channels = []
        lines = content.strip().split('\n')
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            if line.startswith('#EXTINF:'):
                try:
                    # Get the next non-empty line as the URL
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
                    
                    # Parse EXTINF line
                    extinf_content = line[8:]  # Remove '#EXTINF:'
                    
                    channel_name = ""
                    tvg_id = ""
                    tvg_logo = ""
                    group_title = ""
                    tvg_chno = ""
                    
                    if ',' in extinf_content:
                        attr_part, name_part = extinf_content.split(',', 1)
                        channel_name = name_part.strip()
                        
                        # Parse attributes
                        tvg_id_match = re.search(r'tvg-id="([^"]*)"', attr_part)
                        tvg_logo_match = re.search(r'tvg-logo="([^"]*)"', attr_part)
                        group_match = re.search(r'group-title="([^"]*)"', attr_part)
                        chno_match = re.search(r'tvg-chno="([^"]*)"', attr_part)
                        
                        if tvg_id_match:
                            tvg_id = tvg_id_match.group(1)
                        if tvg_logo_match:
                            tvg_logo = tvg_logo_match.group(1)
                        if group_match:
                            group_title = group_match.group(1)
                        if chno_match:
                            tvg_chno = chno_match.group(1)
                    
                    if channel_name and url_line:
                        # Create unique channel ID
                        channel_id = tvg_id if tvg_id else f"{country_code}-{channel_name.lower().replace(' ', '-').replace('&', 'and')}"
                        
                        # Format country name for group
                        country_name = self._get_country_name(country_code)
                        
                        channel = {
                            'id': f"lg-{channel_id}",
                            'name': channel_name,
                            'stream_url': url_line,
                            'logo': tvg_logo,
                            'group': group_title or f"LG {country_name}",
                            'number': int(tvg_chno) if tvg_chno and tvg_chno.isdigit() else None,
                            'description': f"LG {country_name} channel: {channel_name}",
                            'language': self._get_country_language(country_code)
                        }
                        channels.append(channel)
                    
                    i = j + 1
                    
                except Exception as e:
                    self.logger.debug(f"Error parsing M3U line: {e}")
                    i += 1
            else:
                i += 1
        
        return channels
    
    def _get_country_name(self, country_code: str) -> str:
        """Get country name from code"""
        country_names = {
            'us': 'USA',
            'uk': 'UK',
            'gb': 'Great Britain',
            'ca': 'Canada',
            'de': 'Germany',
            'fr': 'France',
            'au': 'Australia',
            'jp': 'Japan',
            'kr': 'Korea',
            'in': 'India',
            'br': 'Brazil',
            'it': 'Italy',
            'es': 'Spain',
            'mx': 'Mexico',
            'ar': 'Argentina',
            'cl': 'Chile',
            'co': 'Colombia',
            'pe': 'Peru',
            'nl': 'Netherlands',
            'se': 'Sweden',
            'no': 'Norway',
            'dk': 'Denmark',
            'fi': 'Finland',
            'pl': 'Poland',
            'ru': 'Russia',
            'cn': 'China',
            'th': 'Thailand',
            'vn': 'Vietnam',
            'id': 'Indonesia',
            'my': 'Malaysia',
            'sg': 'Singapore',
            'ph': 'Philippines',
            'tw': 'Taiwan',
            'hk': 'Hong Kong',
            'za': 'South Africa',
            'eg': 'Egypt',
            'tr': 'Turkey',
            'ae': 'UAE',
            'sa': 'Saudi Arabia',
        }
        return country_names.get(country_code, country_code.upper())
    
    def _get_country_language(self, country_code: str) -> str:
        """Get primary language for country"""
        language_mapping = {
            'us': 'en', 'uk': 'en', 'ca': 'en', 'au': 'en',
            'de': 'de', 'fr': 'fr', 'es': 'es', 'it': 'it',
            'br': 'pt', 'mx': 'es', 'ar': 'es', 'cl': 'es',
            'co': 'es', 'pe': 'es', 'nl': 'nl', 'se': 'sv',
            'no': 'no', 'dk': 'da', 'fi': 'fi', 'pl': 'pl',
            'ru': 'ru', 'cn': 'zh', 'jp': 'ja', 'kr': 'ko',
            'th': 'th', 'vn': 'vi', 'id': 'id', 'my': 'ms',
            'ph': 'en', 'tw': 'zh', 'hk': 'zh', 'in': 'hi',
            'za': 'en', 'eg': 'ar', 'tr': 'tr', 'ae': 'ar',
            'sa': 'ar', 'gb': 'gb'
        }
        return language_mapping.get(country_code, 'en')
    
    def get_channels(self) -> List[Dict[str, Any]]:
        """Get LG channels from all configured countries"""
        try:
            # Check cache first
            if time.time() < self.cache_expiry and self.channels_cache:
                self.logger.debug("Using cached LG channels")
                return self.channels_cache
            
            country_codes = self._get_country_codes()
            self.logger.info(f"Fetching LG channels for countries: {', '.join(country_codes)}")
            
            all_channels = []
            
            for country_code in country_codes:
                try:
                    country_channels = self._fetch_country_m3u(country_code)
                    all_channels.extend(country_channels)
                except Exception as e:
                    self.logger.warning(f"Error fetching LG channels for {country_code}: {e}")
                    continue
            
            # Validate and normalize channels
            valid_channels = []
            for channel in all_channels:
                if self.validate_channel(channel):
                    valid_channels.append(self.normalize_channel(channel))
            
            # Cache results
            if valid_channels:
                self.channels_cache = valid_channels
                self.cache_expiry = time.time() + self.cache_duration
                self.logger.info(f"Successfully processed {len(valid_channels)} LG channels")
            else:
                self.logger.warning("No valid LG channels found")
            
            return valid_channels
            
        except Exception as e:
            self.logger.error(f"Error fetching LG channels: {e}")
            return []
    
    def get_epg_data(self):
        """EPG handled by aggregator"""
        return {}