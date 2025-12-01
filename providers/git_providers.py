"""
Git-based IPTV Provider Implementations
Supports iptv-org/iptv and Free-TV/IPTV repositories with country filtering
"""

import requests
import json
import os
import re
import time
import concurrent.futures
from typing import List, Dict, Any, Set
from urllib.parse import unquote
from .base_provider import BaseProvider

class GitIptvProvider(BaseProvider):
    """Provider for iptv-org/iptv repository"""
    
    def __init__(self):
        super().__init__("git_iptv")
        
        self.repo_api_url = "https://api.github.com/repos/iptv-org/iptv/contents/streams"
        self.raw_base_url = "https://raw.githubusercontent.com/iptv-org/iptv/master/streams"
        
        # Get country filter from environment
        self.country_filter = self._parse_country_filter()
        
        # Country code mapping for flexible filtering
        self.country_mapping = self._build_country_mapping()
        
        # Cache for GitHub API responses
        self.github_cache = {}
        self.cache_duration = 3600  # 1 hour cache for GitHub API
        
    def _parse_country_filter(self) -> Set[str]:
        """Parse GIT_COUNTRY environment variable"""
        git_countries = os.getenv('GIT_COUNTRY', '').strip()
        if not git_countries:
            return set()
        
        # Split by comma and normalize
        countries = []
        for country in git_countries.split(','):
            country = country.strip().lower()
            if country:
                countries.append(country)
        
        return set(countries)
    
    def _build_country_mapping(self) -> Dict[str, List[str]]:
        """Build mapping of country codes to various representations"""
        # Common country mappings (expand as needed)
        mapping = {
            'us': ['us', 'usa', 'united states', 'america'],
            'uk': ['uk', 'gb', 'gbr', 'united kingdom', 'britain'],
            'ca': ['ca', 'can', 'canada'],
            'de': ['de', 'deu', 'germany', 'deutschland'],
            'fr': ['fr', 'fra', 'france'],
            'au': ['au', 'aus', 'australia'],
            'jp': ['jp', 'jpn', 'japan'],
            'in': ['in', 'ind', 'india'],
            'br': ['br', 'bra', 'brazil', 'brasil'],
            'it': ['it', 'ita', 'italy', 'italia'],
            'es': ['es', 'esp', 'spain', 'españa'],
            'mx': ['mx', 'mex', 'mexico'],
            'ru': ['ru', 'rus', 'russia'],
            'cn': ['cn', 'chn', 'china'],
            'kr': ['kr', 'kor', 'south korea', 'korea'],
            'nl': ['nl', 'nld', 'netherlands', 'holland'],
            'se': ['se', 'swe', 'sweden'],
            'no': ['no', 'nor', 'norway'],
            'dk': ['dk', 'dnk', 'denmark'],
            'fi': ['fi', 'fin', 'finland'],
            'pl': ['pl', 'pol', 'poland'],
            'ar': ['ar', 'arg', 'argentina'],
            'cl': ['cl', 'chl', 'chile'],
            'co': ['co', 'col', 'colombia'],
            'pe': ['pe', 'per', 'peru'],
            'za': ['za', 'zaf', 'south africa'],
            'eg': ['eg', 'egy', 'egypt'],
            'tr': ['tr', 'tur', 'turkey'],
            'gr': ['gr', 'grc', 'greece'],
            'pt': ['pt', 'prt', 'portugal'],
            'ie': ['ie', 'irl', 'ireland'],
            'be': ['be', 'bel', 'belgium'],
            'ch': ['ch', 'che', 'switzerland'],
            'at': ['at', 'aut', 'austria'],
            'cz': ['cz', 'cze', 'czech republic'],
            'hu': ['hu', 'hun', 'hungary'],
            'ro': ['ro', 'rou', 'romania'],
            'bg': ['bg', 'bgr', 'bulgaria'],
            'hr': ['hr', 'hrv', 'croatia'],
            'si': ['si', 'svn', 'slovenia'],
            'sk': ['sk', 'svk', 'slovakia'],
            'lt': ['lt', 'ltu', 'lithuania'],
            'lv': ['lv', 'lva', 'latvia'],
            'ee': ['ee', 'est', 'estonia'],
        }
        return mapping
    
    def _matches_country_filter(self, filename: str) -> bool:
        """Check if filename matches country filter"""
        if not self.country_filter:
            return True
        
        filename_lower = filename.lower()
        
        # Check direct matches first
        for country in self.country_filter:
            if country in filename_lower:
                return True
        
        # Check mapped country codes
        for code, variants in self.country_mapping.items():
            if any(variant in self.country_filter for variant in variants):
                # Check if this country code appears in filename
                if code in filename_lower:
                    return True
                # Also check for the variants in filename
                if any(variant in filename_lower for variant in variants):
                    return True
        
        return False
    
    def _fetch_github_directory(self, api_url: str) -> List[Dict]:
        """Fetch directory listing from GitHub API with caching"""
        # Check cache first
        cache_key = api_url
        now = time.time()
        
        if cache_key in self.github_cache:
            cached_data, cached_time = self.github_cache[cache_key]
            if now - cached_time < self.cache_duration:
                return cached_data
        
        try:
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': self.get_user_agent()
            }
            
            # Add GitHub token if available for higher rate limits
            github_token = os.getenv('GITHUB_TOKEN')
            if github_token:
                headers['Authorization'] = f'token {github_token}'
            
            response = self.make_request('GET', api_url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            
            # Cache the result
            self.github_cache[cache_key] = (data, now)
            
            return data
            
        except Exception as e:
            self.logger.error(f"Error fetching GitHub directory {api_url}: {e}")
            return []
    
    def _parse_m3u_content(self, content: str, source_name: str = "") -> List[Dict[str, Any]]:
        """Parse M3U playlist content and extract channel information"""
        channels = []
        lines = content.strip().split('\n')
        
        i = 0
        while i < len(lines):
            line = lines[i].strip()
            
            # Look for EXTINF lines
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
                    
                    # Extract attributes and channel name
                    channel_name = ""
                    attributes = {}
                    
                    # Split by comma to separate attributes from name
                    if ',' in extinf_content:
                        attr_part, name_part = extinf_content.split(',', 1)
                        channel_name = name_part.strip()
                        
                        # Parse attributes
                        attr_matches = re.findall(r'(\w+(?:-\w+)*)="([^"]*)"', attr_part)
                        for key, value in attr_matches:
                            attributes[key] = value
                    else:
                        channel_name = extinf_content.strip()
                    
                    if not channel_name or not url_line:
                        i = j + 1
                        continue
                    
                    # Build channel info
                    channel_id = attributes.get('tvg-id', f"git-iptv-{len(channels)}")
                    logo = attributes.get('tvg-logo', '')
                    group = attributes.get('group-title', source_name or 'IPTV')
                    
                    channel = {
                        'id': f"git-iptv-{channel_id}",
                        'name': channel_name,
                        'stream_url': url_line,
                        'logo': logo,
                        'group': group,
                        'description': f"IPTV channel: {channel_name}" + (f" from {source_name}" if source_name else ""),
                        'language': 'en'  # Default, could be enhanced
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
    
    def _fetch_and_parse_m3u(self, file_info: Dict) -> List[Dict[str, Any]]:
        """Fetch and parse a single M3U file"""
        try:
            file_name = file_info.get('name', '')
            download_url = file_info.get('download_url', '')
            
            if not download_url:
                return []
            
            # Fetch M3U content
            response = self.make_request('GET', download_url)
            response.raise_for_status()
            
            content = response.text
            if not content.strip():
                return []
            
            # Parse M3U content
            source_name = file_name.replace('.m3u', '').replace('_', ' ').title()
            channels = self._parse_m3u_content(content, source_name)
            
            if channels:
                self.logger.debug(f"Parsed {len(channels)} channels from {file_name}")
            
            return channels
            
        except Exception as e:
            self.logger.warning(f"Error fetching M3U file {file_info.get('name', 'unknown')}: {e}")
            return []
    
    def get_channels(self) -> List[Dict[str, Any]]:
        """Get channels from iptv-org/iptv repository"""
        try:
            self.logger.info(f"Fetching Git IPTV channels with country filter: {self.country_filter or 'all'}")
            start_time = time.time()
            
            # Fetch directory listing
            directory_data = self._fetch_github_directory(self.repo_api_url)
            if not directory_data:
                self.logger.error("Failed to fetch Git IPTV directory listing")
                return []
            
            # Filter M3U files based on country filter
            m3u_files = []
            for item in directory_data:
                if (item.get('type') == 'file' and 
                    item.get('name', '').endswith('.m3u') and
                    self._matches_country_filter(item.get('name', ''))):
                    m3u_files.append(item)
            
            if not m3u_files:
                self.logger.warning("No M3U files found matching country filter")
                return []
            
            self.logger.info(f"Found {len(m3u_files)} M3U files to process")
            
            # Process M3U files concurrently
            all_channels = []
            max_workers = min(5, len(m3u_files))  # Limit concurrent requests
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(self._fetch_and_parse_m3u, file_info) for file_info in m3u_files]
                
                for future in concurrent.futures.as_completed(futures, timeout=60):
                    try:
                        channels = future.result(timeout=10)
                        all_channels.extend(channels)
                    except Exception as e:
                        self.logger.warning(f"Error processing M3U file: {e}")
                        continue
            
            elapsed = time.time() - start_time
            self.logger.info(f"Successfully processed {len(all_channels)} Git IPTV channels in {elapsed:.1f}s")
            return all_channels
            
        except Exception as e:
            self.logger.error(f"Error fetching Git IPTV channels: {e}")
            return []

    def get_epg_data(self):
        """EPG handled by aggregator"""
        return {}

class GitFreetvProvider(BaseProvider):
    """Provider for Free-TV/IPTV repository"""
    
    def __init__(self):
        super().__init__("git_freetv")
        
        self.repo_api_url = "https://api.github.com/repos/Free-TV/IPTV/contents/playlists"
        self.raw_base_url = "https://raw.githubusercontent.com/Free-TV/IPTV/master/playlists"
        
        # Get country filter from environment
        self.country_filter = self._parse_country_filter()
        
        # Country code mapping (same as GitIptvProvider)
        self.country_mapping = self._build_country_mapping()
        
        # Cache for GitHub API responses
        self.github_cache = {}
        self.cache_duration = 3600  # 1 hour cache for GitHub API
        
    def _parse_country_filter(self) -> Set[str]:
        """Parse GIT_COUNTRY environment variable"""
        git_countries = os.getenv('GIT_COUNTRY', '').strip()
        if not git_countries:
            return set()
        
        countries = []
        for country in git_countries.split(','):
            country = country.strip().lower()
            if country:
                countries.append(country)
        
        return set(countries)
    
    def _build_country_mapping(self) -> Dict[str, List[str]]:
        """Build mapping of country codes to various representations"""
        # Same mapping as GitIptvProvider
        mapping = {
            'us': ['us', 'usa', 'united states', 'america'],
            'uk': ['uk', 'gb', 'gbr', 'united kingdom', 'britain'],
            'ca': ['ca', 'can', 'canada'],
            'de': ['de', 'deu', 'germany', 'deutschland'],
            'fr': ['fr', 'fra', 'france'],
            'au': ['au', 'aus', 'australia'],
            'jp': ['jp', 'jpn', 'japan'],
            'in': ['in', 'ind', 'india'],
            'br': ['br', 'bra', 'brazil', 'brasil'],
            'it': ['it', 'ita', 'italy', 'italia'],
            'es': ['es', 'esp', 'spain', 'españa'],
            'mx': ['mx', 'mex', 'mexico'],
            'ru': ['ru', 'rus', 'russia'],
            'cn': ['cn', 'chn', 'china'],
            'kr': ['kr', 'kor', 'south korea', 'korea'],
            'nl': ['nl', 'nld', 'netherlands', 'holland'],
            'se': ['se', 'swe', 'sweden'],
            'no': ['no', 'nor', 'norway'],
            'dk': ['dk', 'dnk', 'denmark'],
            'fi': ['fi', 'fin', 'finland'],
            'pl': ['pl', 'pol', 'poland'],
            'ar': ['ar', 'arg', 'argentina'],
            'cl': ['cl', 'chl', 'chile'],
            'co': ['co', 'col', 'colombia'],
            'pe': ['pe', 'per', 'peru'],
            'za': ['za', 'zaf', 'south africa'],
            'eg': ['eg', 'egy', 'egypt'],
            'tr': ['tr', 'tur', 'turkey'],
            'gr': ['gr', 'grc', 'greece'],
            'pt': ['pt', 'prt', 'portugal'],
            'ie': ['ie', 'irl', 'ireland'],
            'be': ['be', 'bel', 'belgium'],
            'ch': ['ch', 'che', 'switzerland'],
            'at': ['at', 'aut', 'austria'],
            'cz': ['cz', 'cze', 'czech republic'],
            'hu': ['hu', 'hun', 'hungary'],
            'ro': ['ro', 'rou', 'romania'],
            'bg': ['bg', 'bgr', 'bulgaria'],
            'hr': ['hr', 'hrv', 'croatia'],
            'si': ['si', 'svn', 'slovenia'],
            'sk': ['sk', 'svk', 'slovakia'],
            'lt': ['lt', 'ltu', 'lithuania'],
            'lv': ['lv', 'lva', 'latvia'],
            'ee': ['ee', 'est', 'estonia'],
        }
        return mapping
    
    def _matches_country_filter(self, filename: str) -> bool:
        """Check if filename matches country filter - Free-TV uses playlist_<country>.m3u8 format"""
        if not self.country_filter:
            return True
        
        filename_lower = filename.lower()
        
        # Remove 'playlist_' prefix and '.m3u8' suffix to get the country part
        if filename_lower.startswith('playlist_') and filename_lower.endswith('.m3u8'):
            country_part = filename_lower[9:-5]  # Remove 'playlist_' and '.m3u8'
            
            # Handle cases like 'usa_vod' -> 'usa'
            if '_' in country_part:
                country_part = country_part.split('_')[0]
            
            # Check direct matches first
            for country in self.country_filter:
                if country == country_part:
                    return True
            
            # Check mapped country codes
            for code, variants in self.country_mapping.items():
                if any(variant in self.country_filter for variant in variants):
                    # Check if this country code matches the filename country part
                    if code == country_part:
                        return True
                    # Also check for the variants matching country part
                    if any(variant == country_part for variant in variants):
                        return True
        
        # Fallback to original matching for other patterns
        # Check direct matches first
        for country in self.country_filter:
            if country in filename_lower:
                return True
        
        # Check mapped country codes
        for code, variants in self.country_mapping.items():
            if any(variant in self.country_filter for variant in variants):
                if code in filename_lower:
                    return True
                if any(variant in filename_lower for variant in variants):
                    return True
        
        return False
    
    def _fetch_github_directory(self, api_url: str) -> List[Dict]:
        """Fetch directory listing from GitHub API with caching"""
        cache_key = api_url
        now = time.time()
        
        if cache_key in self.github_cache:
            cached_data, cached_time = self.github_cache[cache_key]
            if now - cached_time < self.cache_duration:
                return cached_data
        
        try:
            headers = {
                'Accept': 'application/vnd.github.v3+json',
                'User-Agent': self.get_user_agent()
            }
            
            github_token = os.getenv('GITHUB_TOKEN')
            if github_token:
                headers['Authorization'] = f'token {github_token}'
            
            response = self.make_request('GET', api_url, headers=headers)
            response.raise_for_status()
            
            data = response.json()
            self.github_cache[cache_key] = (data, now)
            
            return data
            
        except Exception as e:
            self.logger.error(f"Error fetching GitHub directory {api_url}: {e}")
            return []
    
    def _parse_m3u_content(self, content: str, source_name: str = "") -> List[Dict[str, Any]]:
        """Parse M3U playlist content and extract channel information"""
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
                    attributes = {}
                    
                    if ',' in extinf_content:
                        attr_part, name_part = extinf_content.split(',', 1)
                        channel_name = name_part.strip()
                        
                        # Parse attributes
                        attr_matches = re.findall(r'(\w+(?:-\w+)*)="([^"]*)"', attr_part)
                        for key, value in attr_matches:
                            attributes[key] = value
                    else:
                        channel_name = extinf_content.strip()
                    
                    if not channel_name or not url_line:
                        i = j + 1
                        continue
                    
                    channel_id = attributes.get('tvg-id', f"git-freetv-{len(channels)}")
                    logo = attributes.get('tvg-logo', '')
                    group = attributes.get('group-title', source_name or 'Free TV')
                    
                    channel = {
                        'id': f"git-freetv-{channel_id}",
                        'name': channel_name,
                        'stream_url': url_line,
                        'logo': logo,
                        'group': group,
                        'description': f"Free TV channel: {channel_name}" + (f" from {source_name}" if source_name else ""),
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
    
    def _fetch_and_parse_m3u(self, file_info: Dict) -> List[Dict[str, Any]]:
        """Fetch and parse a single M3U8 file"""
        try:
            file_name = file_info.get('name', '')
            download_url = file_info.get('download_url', '')
            
            if not download_url:
                return []
            
            response = self.make_request('GET', download_url)
            response.raise_for_status()
            
            content = response.text
            if not content.strip():
                return []
            
            # Extract source name from filename like 'playlist_canada.m3u8' -> 'Canada'
            source_name = file_name.replace('playlist_', '').replace('.m3u8', '').replace('_', ' ').title()
            if source_name.lower() == 'usa':
                source_name = 'USA'
            
            channels = self._parse_m3u_content(content, source_name)
            
            if channels:
                self.logger.debug(f"Parsed {len(channels)} channels from {file_name}")
            
            return channels
            
        except Exception as e:
            self.logger.warning(f"Error fetching M3U8 file {file_info.get('name', 'unknown')}: {e}")
            return []
    
    def get_channels(self) -> List[Dict[str, Any]]:
        """Get channels from Free-TV/IPTV repository"""
        try:
            self.logger.info(f"Fetching Git Free TV channels with country filter: {self.country_filter or 'all'}")
            start_time = time.time()
            
            directory_data = self._fetch_github_directory(self.repo_api_url)
            if not directory_data:
                self.logger.error("Failed to fetch Git Free TV directory listing")
                return []
            
            # Filter M3U8 files based on country filter
            m3u_files = []
            for item in directory_data:
                if (item.get('type') == 'file' and 
                    item.get('name', '').endswith('.m3u8') and
                    self._matches_country_filter(item.get('name', ''))):
                    m3u_files.append(item)
            
            if not m3u_files:
                self.logger.warning("No M3U8 files found matching country filter")
                return []
            
            self.logger.info(f"Found {len(m3u_files)} M3U8 files to process")
            
            # Process M3U8 files concurrently
            all_channels = []
            max_workers = min(5, len(m3u_files))
            
            with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                futures = [executor.submit(self._fetch_and_parse_m3u, file_info) for file_info in m3u_files]
                
                for future in concurrent.futures.as_completed(futures, timeout=60):
                    try:
                        channels = future.result(timeout=10)
                        all_channels.extend(channels)
                    except Exception as e:
                        self.logger.warning(f"Error processing M3U8 file: {e}")
                        continue
            
            elapsed = time.time() - start_time
            self.logger.info(f"Successfully processed {len(all_channels)} Git Free TV channels in {elapsed:.1f}s")
            return all_channels
            
        except Exception as e:
            self.logger.error(f"Error fetching Git Free TV channels: {e}")
            return []
    
    def get_epg_data(self):
        """EPG handled by aggregator"""
        return {}