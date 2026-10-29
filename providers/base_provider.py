"""
Base Provider Class - Optimized for Performance with urllib3 compatibility
"""

from abc import ABC, abstractmethod
from typing import List, Dict, Any, Optional
import logging
import sys
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

class BaseProvider(ABC):
    """Base class for all streaming service providers - optimized"""
    
    def __init__(self, name: str):
        self.name = name
        self.logger = logging.getLogger(f"providers.{name}")
        # Set recursion limit to prevent stack overflow
        if sys.getrecursionlimit() < 2000:
            sys.setrecursionlimit(2000)
        
        # Create optimized session with connection pooling
        self.session = self._create_optimized_session()

        # Optional upper bound (seconds) on how long the aggregated channel cache may hold this provider's output
        self.max_cache_age: Optional[int] = None
        
    def _create_optimized_session(self) -> requests.Session:
        """Create an optimized requests session with connection pooling and retries"""
        session = requests.Session()
        
        try:
            # Try newer urllib3 parameter name first
            retry_strategy = Retry(
                total=2,
                status_forcelist=[429, 500, 502, 503, 504],
                allowed_methods=["HEAD", "GET", "OPTIONS"],  # New parameter name
                backoff_factor=0.3
            )
        except TypeError:
            try:
                # Fall back to older parameter name
                retry_strategy = Retry(
                    total=2,
                    status_forcelist=[429, 500, 502, 503, 504],
                    method_whitelist=["HEAD", "GET", "OPTIONS"],  # Old parameter name
                    backoff_factor=0.3
                )
            except TypeError:
                # If both fail, create a simple retry without method restrictions
                retry_strategy = Retry(
                    total=2,
                    status_forcelist=[429, 500, 502, 503, 504],
                    backoff_factor=0.3
                )
        
        # Configure adapter with connection pooling
        adapter = HTTPAdapter(
            pool_connections=10,
            pool_maxsize=20,
            max_retries=retry_strategy
        )
        
        session.mount("http://", adapter)
        session.mount("https://", adapter)
        
        # Set default headers
        session.headers.update({
            'User-Agent': self.get_user_agent()
        })
        
        return session
    
    @abstractmethod
    def get_channels(self) -> List[Dict[str, Any]]:
        """Get list of available channels"""
        pass
    
    @abstractmethod
    def get_epg_data(self) -> Dict[str, List[Dict[str, Any]]]:
        """Get EPG data for channels"""
        pass
    
    def clear_cache(self) -> None:
        """Expire any provider-level caches so the next fetch hits upstream"""
        pass
    
    def validate_channel(self, channel: Dict[str, Any]) -> bool:
        """Validate that a channel has required fields"""
        try:
            required_fields = ['id', 'name', 'stream_url']
            
            for field in required_fields:
                if not channel.get(field):
                    return False
                    
            return True
        except Exception:
            return False
    
    def normalize_channel(self, channel: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize channel data to ensure consistent format"""
        try:
            normalized = {}
            
            # Required fields
            normalized['id'] = str(channel.get('id', '')) if channel.get('id') is not None else ''
            normalized['name'] = str(channel.get('name', '')).strip() if channel.get('name') is not None else ''
            normalized['stream_url'] = str(channel.get('stream_url', '')).strip() if channel.get('stream_url') is not None else ''
            
            # Optional fields with defaults
            normalized['logo'] = str(channel.get('logo', '')).strip() if channel.get('logo') else ''
            normalized['group'] = str(channel.get('group', 'General')).strip() if channel.get('group') else 'General'
            normalized['description'] = str(channel.get('description', '')).strip() if channel.get('description') else ''
            normalized['language'] = str(channel.get('language', 'en')).strip() if channel.get('language') else 'en'
            
            # Handle number field
            if channel.get('number') is not None:
                try:
                    normalized['number'] = int(channel['number'])
                except (ValueError, TypeError):
                    pass
            
            # Remove empty strings except for required fields
            return {k: v for k, v in normalized.items() if v != '' or k in ['id', 'name', 'stream_url']}
            
        except Exception as e:
            self.logger.error(f"Error normalizing channel: {e}")
            return channel
    
    def validate_programme(self, programme: Dict[str, Any]) -> bool:
        """Validate that a programme has required fields"""
        try:
            required_fields = ['title', 'start', 'stop']
            return all(programme.get(field) for field in required_fields)
        except Exception:
            return False
    
    def normalize_programme(self, programme: Dict[str, Any]) -> Dict[str, Any]:
        """Normalize programme data to ensure consistent format"""
        try:
            normalized = {}
            
            # Required fields
            normalized['title'] = str(programme.get('title', '')).strip()
            normalized['start'] = str(programme.get('start', '')).strip()
            normalized['stop'] = str(programme.get('stop', '')).strip()
            
            # Optional fields
            if programme.get('description'):
                normalized['description'] = str(programme['description']).strip()
            if programme.get('category'):
                normalized['category'] = str(programme['category']).strip()
            if programme.get('episode'):
                normalized['episode'] = str(programme['episode']).strip()
            
            return {k: v for k, v in normalized.items() if v}
            
        except Exception as e:
            self.logger.error(f"Error normalizing programme: {e}")
            return programme
    
    def get_user_agent(self) -> str:
        """Get a standard user agent string"""
        return 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36'
    
    def get_timeout(self) -> tuple:
        """Get standard timeout values for requests (connect, read)"""
        return (10, 30)  # Balanced timeout values
    
    def make_request(self, method: str, url: str, **kwargs) -> requests.Response:
        """Make HTTP request using the optimized session"""
        try:
            # Set timeout if not provided
            if 'timeout' not in kwargs:
                kwargs['timeout'] = self.get_timeout()
            
            response = self.session.request(method, url, **kwargs)
            return response
        except Exception as e:
            self.logger.error(f"Request failed for {url}: {e}")
            raise
    
    def __del__(self):
        """Cleanup session when provider is destroyed"""
        if hasattr(self, 'session'):
            self.session.close()