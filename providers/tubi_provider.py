"""
Tubi Provider Implementation - Based on Working tubi-for-channels repo
"""

import json
import html
import concurrent.futures
import uuid
import time
import os
import re
from datetime import datetime
from typing import List, Dict, Any, Optional
from urllib.parse import unquote
from .base_provider import BaseProvider

# the public sitemap lists every movie and series, paged
_SITEMAP_FIRST   = "https://tubitv.com/sitemaps/sitemap/{kind}/"
_SITEMAP_PAGE    = "https://tubitv.com/sitemaps/sitemap/{kind}/page/{page}/"
_SITEMAP_WORKERS = 8

class TubiProvider(BaseProvider):
    """Provider for Tubi TV channels"""
    
    def __init__(self):
        super().__init__("tubi")
        
        self.device_id = str(uuid.uuid4())
        self.access_token = None
        self.token_expires_at = 0
        
        self.headers = {
            'accept': '*/*',
            'accept-language': 'en-US',
            'content-type': 'application/json',
            'origin': 'https://tubitv.com',
            'priority': 'u=1, i',
            'referer': 'https://tubitv.com/',
            'sec-ch-ua': '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
            'sec-ch-ua-mobile': '?0',
            'sec-ch-ua-platform': '"Windows"',
            'sec-fetch-dest': 'empty',
            'sec-fetch-mode': 'cors',
            'sec-fetch-site': 'cross-site',
            'user-agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36',
        }
        
        # Try to get user credentials from environment
        self.user = os.getenv("TUBI_USER")
        self.password = os.getenv("TUBI_PASS")
    
    def replace_quotes(self, match):
        """Helper function for JSON cleaning"""
        return '"' + match.group(1).replace('"', r'\"') + '"'
    
    def channel_id_list_anon(self):
        """Get channel list from anonymous Tubi live page - based on working implementation"""
        url = "https://tubitv.com/live"
        error = None
        
        try:
            response = self.session.get(url, headers=self.headers, timeout=self.get_timeout())
        except Exception as e:
            error = f"channel_id_list_anon Exception: {type(e).__name__}"

        if error: 
            return None, None, error
        
        if response.status_code != 200:
            return None, None, f"tubitv.com/live HTTP failure {response.status_code}: {response.text}"
        
        html_content = response.text

        # Locate the <script> block that opens with window.__data
        try:
            script_match = re.search(r'<script[^>]*>\s*(window\.__data)', html_content)
            if not script_match:
                return None, None, "Error: No window.__data script found"
            
            # Bound the script's contents by its closing tag
            script_start = script_match.start(1)
            script_end = html_content.find("</script>", script_start)
            if script_end == -1:
                script_end = len(html_content)
            target_script = html_content[script_start:script_end]
            
            # Extract JSON part from the string
            start_index = target_script.find("{")
            end_index = target_script.rfind("}") + 1
            
            # Extract the JSON part
            json_string = target_script[start_index:end_index]
            
            # Replace 'undefined' with 'null' to make it valid JSON
            json_string = re.sub(r'\bundefined\b', 'null', json_string)
            
            # More corrections for valid JSON (from working implementation)
            pattern = r'(new\s+Date\("[^"]*"\)|read\s+Date\("[^"]*"\))'
            json_string = re.sub(pattern, self.replace_quotes, json_string)
            
            try:
                data_json = json.loads(json_string)
            except Exception as e:
                return None, None, f"JSON parsing error: {type(e).__name__}"
            
            epg = data_json.get('epg')
            if not epg:
                return None, None, "No EPG data found"
                
            contentIdsByContainer = epg.get('contentIdsByContainer')
            if not contentIdsByContainer:
                return None, None, "No contentIdsByContainer found"
                
            skip_slugs = ['favorite_linear_channels', 'recommended_linear_channels', 'featured_channels', 'recently_added_channels']
            
            # older pages key the container lists by region, newer ones send the container list itself
            if isinstance(contentIdsByContainer, dict):
                containers = [item for rows in contentIdsByContainer.values() for item in (rows or [])]
                group_listing = contentIdsByContainer.get("tubitv_us_linear", [])
            else:
                containers = contentIdsByContainer
                group_listing = contentIdsByContainer

            channel_list = []
            for item in containers:
                if item['container_slug'] not in skip_slugs:
                    channel_list.extend(item["contents"])

            channel_list = list(set(channel_list))
            self.logger.info(f'Number of streams available: {len(channel_list)}')

            # Extract group information
            groups = {}
            for elem in group_listing:
                if elem["container_slug"] not in skip_slugs:
                    groups.update({elem['name']: elem['contents']})
            
            return channel_list, groups, None
            
        except Exception as e:
            self.logger.error(f"Error in channel_id_list_anon: {e}")
            return None, None, str(e)
    
    def read_epg_anon(self):
        """Get EPG data anonymously - based on working implementation"""
        self.logger.info("Updating Anonymous Channel List")
        channel_id_list, groups, error = self.channel_id_list_anon()
        if error: 
            return None, None, error

        self.logger.info("Retrieving EPG Data")
        epg_data = []

        # Process channels in batches like the working implementation
        group_size = 150
        grouped_id_values = [channel_id_list[i:i + group_size] for i in range(0, len(channel_id_list), group_size)]

        def fetch_batch(group):
            params = {"content_id": ','.join(map(str, group))}
            
            response = self.session.get('https://tubitv.com/oz/epg/programming', params=params, headers=self.headers, timeout=self.get_timeout())
            if response.status_code != 200:
                self.logger.warning(f"EPG API failed for batch: {response.status_code}")
                return []
            
            return response.json().get('rows', [])
        
        batch_rows = {}
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max(1, min(4, len(grouped_id_values))))
        try:
            futures = {executor.submit(fetch_batch, group): index for index, group in enumerate(grouped_id_values)}
            
            for future in concurrent.futures.as_completed(futures, timeout=60):
                try:
                    batch_rows[futures[future]] = future.result()
                except Exception as e:
                    self.logger.warning(f"Error processing EPG batch: {e}")
        except concurrent.futures.TimeoutError:
            self.logger.warning(f"Tubi: timed out, keeping {len(batch_rows)} of {len(grouped_id_values)} EPG batches collected so far")
        finally:
            executor.shutdown(wait=False, cancel_futures=True)
        
        # Merge in batch order so the output stays deterministic
        for index in sorted(batch_rows):
            epg_data.extend(batch_rows[index])

        # Handle channels with no video resources
        for elem in epg_data:
            if elem.get('video_resources') == []:
                self.logger.warning(f"No Video Data for {elem.get('title', '')}")
                elem['video_resources'] = [{"manifest": {"url": ""}}]

        # Map each content id to the groups it belongs to
        id_groups = {}
        for group_name, values in (groups or {}).items():
            for value in set(values):
                id_groups.setdefault(value, []).append(group_name)

        # Create channel list in the format expected
        channel_list = []
        for elem in epg_data:
            try:
                content_id = str(elem.get('content_id'))
                title = elem.get('title', '')
                
                if not content_id or not title:
                    continue
                
                # Get video URL
                video_resources = elem.get('video_resources', [])
                if not video_resources or not video_resources[0].get('manifest', {}).get('url'):
                    continue
                
                url = f"{unquote(video_resources[0]['manifest']['url'])}&content_id={content_id}"
                
                # Get logo
                logo = ''
                images = elem.get('images', {})
                if images.get('thumbnail'):
                    if isinstance(images['thumbnail'], list):
                        logo = images['thumbnail'][0] if images['thumbnail'] else ''
                    else:
                        logo = images['thumbnail']
                
                channel_info = {
                    'channel-id': content_id,
                    'name': title,
                    'logo': logo,
                    'url': url,
                    'tmsid': elem.get('gracenote_id', None)
                }
                
                # Add group information
                id = content_id
                g_list = id_groups.get(id, [])
                channel_info['group'] = g_list
                
                channel_list.append(channel_info)
                
            except Exception as e:
                self.logger.warning(f"Error processing channel {elem.get('content_id', 'unknown')}: {e}")
                continue

        return channel_list, epg_data, None

    def get_channels(self) -> List[Dict[str, Any]]:
        """Get Tubi channels using the working implementation approach"""
        try:
            # Use the anonymous method that we know works
            channel_list, epg_data, error = self.read_epg_anon()
            if error:
                self.logger.error(f"Failed to get Tubi channels: {error}")
                return []
            
            if not channel_list:
                self.logger.warning("No Tubi channels found")
                return []
            
            # Convert to our expected format
            processed_channels = []
            for channel in channel_list:
                try:
                    channel_info = {
                        'id': f"tubi-{channel['channel-id']}",
                        'name': channel['name'],
                        'stream_url': channel['url'],
                        'logo': channel.get('logo', ''),
                        'group': ';'.join(channel.get('group', ['Tubi'])) if channel.get('group') else 'Tubi',
                        'description': f"Tubi channel: {channel['name']}",
                        'language': 'en'
                    }
                    
                    if self.validate_channel(channel_info):
                        processed_channels.append(self.normalize_channel(channel_info))
                        
                except Exception as e:
                    self.logger.warning(f"Error processing Tubi channel: {e}")
                    continue
            
            self.logger.info(f"Successfully processed {len(processed_channels)} Tubi channels")
            return processed_channels
            
        except Exception as e:
            self.logger.error(f"Error fetching Tubi channels: {e}")
            return []
    
    def get_epg_data(self):
        """EPG handled by aggregator"""
        return {}

    # ── VOD ───────────────────────────────────────────────────────────────────

    def _get_page(self, url: str) -> str:
        """GET a tubitv.com page as text; empty on any failure"""
        try:
            response = self.session.get(url, headers=self.headers, timeout=self.get_timeout())
            if response.status_code != 200:
                self.logger.debug(f"Tubi page {url} returned {response.status_code}")
                return ''
            return response.text
        except Exception as e:
            self.logger.debug(f"Tubi page {url} failed: {e}")
            return ''

    def _page_json(self, html_content: str, var_name: str):
        """Pull the window.<var_name> object out of a page, cleaned up into JSON; None when missing"""
        if not html_content:
            return None

        match = re.search(r'window\.' + re.escape(var_name) + r'\s*=\s*', html_content)
        if not match:
            return None

        # bound it by the closing script tag
        start = match.end()
        end = html_content.find("</script>", start)
        if end == -1:
            end = len(html_content)
        json_string = html_content[start:end]

        # same clean-up as the live page: undefined -> null, Date() calls -> strings
        json_string = re.sub(r'\bundefined\b', 'null', json_string)
        json_string = re.sub(r'(new\s+Date\("[^"]*"\)|read\s+Date\("[^"]*"\))', self.replace_quotes, json_string)

        # raw_decode stops at the end of the object, so anything after it in the script is ignored
        try:
            data, _ = json.JSONDecoder().raw_decode(json_string)
            return data
        except Exception as e:
            self.logger.debug(f"Tubi {var_name} parse error: {type(e).__name__}")
            return None

    def _sitemap_links(self, kind: str) -> Dict[str, str]:
        """Return id -> title for every title across one of the sitemaps (movies or series)"""
        link_re = re.compile(r'<a[^>]+href="/' + kind + r'/(\d+)/[^"]*"[^>]*>(.*?)</a>', re.DOTALL)

        first = self._get_page(_SITEMAP_FIRST.format(kind=kind))
        if not first:
            self.logger.warning(f"Tubi {kind} sitemap unavailable")
            return {}

        # the pager links to the last page
        pages = [int(p) for p in re.findall(r'/sitemaps/sitemap/' + kind + r'/page/(\d+)/', first)]
        last = max(pages) if pages else 1

        found: Dict[str, str] = {}

        def _collect(text: str) -> None:
            for item_id, title in link_re.findall(text or ''):
                title = html.unescape(re.sub(r'<[^>]+>', '', title)).strip()
                if title:
                    found.setdefault(item_id, title)

        _collect(first)

        urls = [_SITEMAP_PAGE.format(kind=kind, page=page) for page in range(2, last + 1)]
        if urls:
            with concurrent.futures.ThreadPoolExecutor(max_workers=_SITEMAP_WORKERS) as executor:
                # map keeps page order so the result is deterministic
                for text in executor.map(self._get_page, urls):
                    _collect(text)

        self.logger.info(f"Tubi {kind} sitemap: {len(found)} titles across {last} pages")
        return found

    def get_vod(self) -> Dict[str, List[Dict[str, Any]]]:
        """Return Tubi's on-demand movies and series from the public sitemap"""
        movies = [
            {
                'id':         item_id,
                'name':       title,
                'group':      'Movies',
                'stream_url': f"/play/tubi/vod:movies:{item_id}",
            }
            for item_id, title in self._sitemap_links('movies').items()
        ]

        series = [
            {
                'id':    item_id,
                'name':  title,
                'group': 'Series',
            }
            for item_id, title in self._sitemap_links('series').items()
        ]

        self.logger.info(f"Tubi VOD: {len(movies)} movies, {len(series)} series")
        return {'movies': movies, 'series': series}

    def get_series_episodes(self, series_id: str) -> Dict[int, List[Dict[str, Any]]]:
        """Return a Tubi series' episodes by season, read from the series page"""
        if not series_id or not series_id.isdigit():
            return {}

        state = self._page_json(self._get_page(f"https://tubitv.com/series/{series_id}/"), '__REACT_QUERY_STATE__')
        if not isinstance(state, dict):
            self.logger.warning(f"Tubi series {series_id}: no query state on the page")
            return {}

        # the query holding the season list
        seasons_data = []
        for query in state.get('queries') or []:
            data = ((query or {}).get('state') or {}).get('data')
            if isinstance(data, dict) and isinstance(data.get('seasons'), list) and data['seasons']:
                seasons_data = data['seasons']
                break

        seasons: Dict[int, List[Dict[str, Any]]] = {}
        for season in seasons_data:
            try:
                season_num = int(season.get('number'))
            except (TypeError, ValueError):
                continue

            episodes = []
            for position, ep in enumerate(season.get('episodes') or [], 1):
                ep_id = str((ep or {}).get('id') or '')
                if not ep_id.isdigit():
                    continue

                try:
                    ep_num = int(ep.get('num') or position)
                except (TypeError, ValueError):
                    ep_num = position

                try:
                    duration = int(ep.get('duration') or 0)
                except (TypeError, ValueError):
                    duration = 0

                episodes.append({
                    'id':            ep_id,
                    'title':         str(ep.get('title') or '').strip(),
                    'episode_num':   ep_num,
                    'stream_url':    f"/play/tubi/vod:tv-shows:{ep_id}",
                    'plot':          str(ep.get('description') or '').strip(),
                    'duration_secs': duration,
                })

            if episodes:
                seasons[season_num] = episodes

        return seasons

    def resolve_stream(self, channel_id: str) -> Optional[str]:
        """Resolve /play/tubi/vod:<movies|tv-shows>:<id> to the title's unencrypted HLS manifest"""
        if not channel_id.startswith('vod:'):
            return None

        kind, _, item_id = channel_id[4:].partition(':')
        if kind not in ('movies', 'tv-shows') or not item_id.isdigit():
            return None

        data = self._page_json(self._get_page(f"https://tubitv.com/{kind}/{item_id}/"), '__data')
        video = ((((data or {}).get('video') or {}).get('byId') or {}).get(item_id)) or {}

        # hlsv3 first, then any hlsv6 that has no license server (no DRM)
        for wanted in ('hlsv3', 'hlsv6'):
            for resource in video.get('video_resources') or []:
                if resource.get('type') != wanted or resource.get('license_server'):
                    continue

                url = str((resource.get('manifest') or {}).get('url') or '').strip()
                if url:
                    return f"https:{url}" if url.startswith('//') else url

        self.logger.warning(f"Tubi {kind}/{item_id}: no unencrypted HLS stream found")
        return None