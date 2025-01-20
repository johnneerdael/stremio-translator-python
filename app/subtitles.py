import json
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import asyncio
import aiohttp
from datetime import datetime, timedelta
from difflib import SequenceMatcher
import re

class SubtitleEntry:
    def __init__(self, start: int, text: str):
        self.start = start  # Start time in milliseconds
        self.text = text
        self.translated_text: Optional[str] = None

    def to_dict(self) -> Dict:
        return {
            "start": self.start,
            "text": self.translated_text or self.text
        }

class SubtitleProcessor:
    # Class-level storage
    _user_rate_limits = {}
    _cache_lock = asyncio.Lock()
    _rate_limit_cleanup_lock = asyncio.Lock()
    _last_cleanup = datetime.now()
    
    def __init__(self, api_key: str, app_name: str = "Stremio AI Translator"):
        self.api_key = api_key
        self.app_name = app_name
        self.base_url = "https://api.opensubtitles.com/api/v1"
        self.batch_size = 15  # Free tier: 15 requests per second
        self.window_size = 60  # 1 minute window
        self.buffer_time = 2 * 60 * 1000  # 2 minutes buffer in milliseconds
        self.cache_ttl = 7 * 24 * 60 * 60  # 7 days in seconds
        self.cleanup_interval = 60 * 60  # Cleanup every hour

    async def fetch_subtitles(self, type: str, id: str) -> List[SubtitleEntry]:
        """Fetch subtitles from OpenSubtitles"""
        try:
            # Parse IMDB ID and episode info if series
            imdb_id = id
            season = None
            episode = None
            if type == 'series' and ':' in id:
                parts = id.split(':')
                imdb_id = parts[0]
                if len(parts) > 2:
                    season = parts[1]
                    episode = parts[2]

            # Build search query with optimized parameters
            search_params = {
                'languages': 'en',  # English only
                'machine_translated': 'exclude',  # Exclude machine translations
                'hearing_impaired': 'exclude',  # Exclude SDH/CC subtitles when possible
                'type': 'movie' if type == 'movie' else 'episode',  # Specific content type
                'order_by': 'download_count',  # Most downloaded first as quality indicator
                'trusted_sources': 'include'  # Prefer trusted uploaders
            }

            # Add content identifiers
            if type == 'series':
                # For TV shows, use parent_imdb_id with season/episode
                search_params['parent_imdb_id'] = imdb_id.replace('tt', '')
                if season and episode:
                    search_params['season_number'] = int(season)
                    search_params['episode_number'] = int(episode)
            else:
                # For movies, use direct imdb_id
                search_params['imdb_id'] = imdb_id.replace('tt', '')

            print(f"OpenSubtitles search params: {json.dumps(search_params, indent=2)}")
            
            # Set up headers for API requests
            headers = {
                'Api-Key': self.api_key,
                'Content-Type': 'application/json',
                'User-Agent': f"{self.app_name}"
            }

            async with aiohttp.ClientSession() as session:
                # Search for subtitles
                async with session.get(
                    f"{self.base_url}/subtitles",
                    params=search_params,
                    headers=headers
                ) as response:
                    if response.status != 200:
                        error_text = await response.text()
                        print(f"OpenSubtitles API error: {error_text}")
                        raise Exception(f"API error: {response.status} - {error_text}")
                    
                    data = await response.json()
                    print(f"OpenSubtitles search results: {json.dumps(data, indent=2)}")
                    
                    if not data.get('data'):
                        raise Exception("No subtitles found")

                # Extract video filename from parameters if available
                video_filename = None
                if '&videoSize=' in id:
                    try:
                        params = dict(p.split('=') for p in id.split('&'))
                        if 'filename' in params:
                            video_filename = params['filename']
                    except:
                        pass

                # Filter out foreign parts only subtitles unless that's all we have
                normal_subs = [s for s in data['data'] if not s.get('attributes', {}).get('foreign_parts_only', False)]
                subtitles = normal_subs if normal_subs else data['data']

                # Find best matching subtitle
                best_subtitle = None
                best_match_ratio = 0

                print("Comparing subtitles for video:", video_filename or "Using embedded English subtitles")
                
                for subtitle in subtitles:
                    # Get subtitle filename
                    sub_filename = subtitle.get('attributes', {}).get('release', '') or subtitle.get('attributes', {}).get('files', [{}])[0].get('file_name', '')
                    
                    if video_filename and sub_filename:
                        # Clean filenames for comparison
                        clean_video = re.sub(r'[^\w\s]', '', video_filename.lower())
                        clean_sub = re.sub(r'[^\w\s]', '', sub_filename.lower())
                        
                        # Calculate similarity ratio
                        ratio = SequenceMatcher(None, clean_video, clean_sub).ratio()
                        print(f"Subtitle: {sub_filename}")
                        print(f"Similarity: {ratio * 100:.2f}%")
                        print(f"Foreign parts only: {subtitle.get('attributes', {}).get('foreign_parts_only', False)}")
                        
                        # Update best match if this is better
                        if ratio > best_match_ratio:
                            best_match_ratio = ratio
                            best_subtitle = subtitle

                if not best_subtitle:
                    # If no filename matches, use the most downloaded subtitle
                    best_subtitle = max(subtitles, key=lambda s: s.get('attributes', {}).get('download_count', 0))
                    print(f"No filename match found, using most downloaded subtitle")

                # Get file ID for download
                file_id = best_subtitle.get('attributes', {}).get('files', [{}])[0].get('file_id')
                if not file_id:
                    raise Exception("Could not get file ID from subtitle")

                print(f"Selected subtitle: {best_subtitle.get('attributes', {}).get('release', '')}")
                print(f"Download count: {best_subtitle.get('attributes', {}).get('download_count', 0)}")
                print(f"Match ratio: {best_match_ratio * 100:.2f}%")
                print(f"File ID: {file_id}")

                # Download subtitle
                async with session.post(
                    f"{self.base_url}/download",
                    headers=headers,
                    json={
                        'file_id': file_id,
                        'sub_format': 'srt'  # Request SRT format
                    }
                ) as download_response:
                    if download_response.status != 200:
                        error_text = await download_response.text()
                        raise Exception(f"Download error: {download_response.status} - {error_text}")
                    
                    download_data = await download_response.json()
                    print(f"Download response: {json.dumps(download_data, indent=2)}")
                    
                    # Get subtitle content from temporary URL
                    async with session.get(download_data['link']) as content_response:
                        if content_response.status != 200:
                            raise Exception(f"Content download failed: {content_response.status}")
                        
                        srt_content = await content_response.text()
                        return self.parse_srt(srt_content)
                        
        except Exception as e:
            print(f"Error fetching subtitles: {str(e)}")
            raise

    def parse_srt(self, content: str) -> List[SubtitleEntry]:
        """Parse SRT format into subtitle entries"""
        entries = []
        lines = content.strip().split('\n\n')
        
        for block in lines:
            if not block.strip():
                continue
            
            parts = block.split('\n')
            if len(parts) < 3:
                continue
            
            try:
                # Parse timecode
                times = parts[1].split(' --> ')
                start_time, end_time = [self.parse_timecode(t) for t in times]
                start_ms = int(start_time.total_seconds() * 1000)
                
                # Get text
                text = '\n'.join(parts[2:]).strip()
                if text:  # Only add if there's actual text
                    entries.append(SubtitleEntry(int(start_ms), text))
            except Exception as e:
                print(f"Error parsing subtitle entry: {str(e)}")
                continue
        
        return sorted(entries, key=lambda x: x.start)

    def parse_timecode(self, timecode):
        """Parse timecode string into timedelta"""
        parts = timecode.split(':')
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds, milliseconds = [int(p) for p in parts[2].split(',')]
        return timedelta(hours=hours, minutes=minutes, seconds=seconds, milliseconds=milliseconds)

    def prioritize_subtitles(self, entries: List[SubtitleEntry]) -> List[List[SubtitleEntry]]:
        """Split subtitles into priority batches"""
        if not entries:
            return []

        # First batch: First 2 minutes of subtitles
        first_batch = []
        later_batches = []
        two_minutes = 2 * 60 * 1000  # 2 minutes in milliseconds

        for entry in entries:
            if entry.start <= two_minutes:
                first_batch.append(entry)
            else:
                later_batches.append(entry)

        # Split later subtitles into batches
        result = [first_batch]
        current_batch = []
        
        for entry in later_batches:
            current_batch.append(entry)
            if len(current_batch) >= self.batch_size:
                result.append(current_batch)
                current_batch = []
        
        if current_batch:
            result.append(current_batch)

        return result

    async def process_batch(self, batch: List[SubtitleEntry], translate_fn, config_b64: str) -> None:
        """Process a batch of subtitles with user-specific rate limiting"""
        now = datetime.now()
        
        # Get user-specific rate limit data
        last_batch_time, requests_in_window = self._get_user_rate_limit(config_b64)
        
        # Reset counter if window has passed
        if (now - last_batch_time) > timedelta(seconds=self.window_size):
            requests_in_window = 0
            last_batch_time = now

        # Check rate limit
        if requests_in_window >= self.batch_size:
            wait_time = self.window_size - (now - last_batch_time).seconds
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                requests_in_window = 0
                last_batch_time = datetime.now()

        # Process batch
        tasks = []
        for entry in batch:
            if not entry.translated_text:  # Only translate if not already translated
                tasks.append(translate_fn(entry.text))
                requests_in_window += 1

        # Update user rate limit data
        self._update_user_rate_limit(config_b64, last_batch_time, requests_in_window)

        translations = await asyncio.gather(*tasks)
        for entry, translation in zip(batch, translations):
            entry.translated_text = translation

    async def save_cache(self, entries: List[SubtitleEntry], cache_path: Path) -> None:
        """Save translated subtitles to cache with TTL"""
        async with self._cache_lock:
            try:
                subtitles = {
                    "subtitles": [entry.to_dict() for entry in entries],
                    "timestamp": datetime.now().timestamp()
                }
                # Write to temporary file first
                temp_path = cache_path.with_suffix('.tmp')
                temp_path.write_text(json.dumps(subtitles, ensure_ascii=False))
                # Atomic rename
                temp_path.replace(cache_path)
                
                # Trigger cleanup if needed
                await self._cleanup_old_files()
            except Exception as e:
                print(f"Cache save error: {str(e)}")
                if temp_path.exists():
                    temp_path.unlink()
                raise

    async def load_cache(self, cache_path: Path) -> Optional[Dict]:
        """Load translated subtitles from cache if not expired"""
        async with self._cache_lock:
            if not cache_path.exists():
                return None
                
            try:
                data = json.loads(cache_path.read_text())
                timestamp = data.get("timestamp", 0)
                
                # Check if cache has expired
                if datetime.now().timestamp() - timestamp > self.cache_ttl:
                    cache_path.unlink()  # Delete expired cache
                    return None
                    
                return {"subtitles": data["subtitles"]}
            except Exception as e:
                print(f"Cache error: {str(e)}")
                return None

    def _get_user_rate_limit(self, config_b64: str) -> Tuple[datetime, int]:
        """Get user-specific rate limit data"""
        if config_b64 not in self._user_rate_limits:
            self._user_rate_limits[config_b64] = {
                "last_batch_time": datetime.now(),
                "requests_in_window": 0
            }
        return (
            self._user_rate_limits[config_b64]["last_batch_time"],
            self._user_rate_limits[config_b64]["requests_in_window"]
        )

    def _update_user_rate_limit(self, config_b64: str, batch_time: datetime, requests: int) -> None:
        """Update user-specific rate limit data"""
        self._user_rate_limits[config_b64] = {
            "last_batch_time": batch_time,
            "requests_in_window": requests,
            "last_access": datetime.now()
        }

    async def _cleanup_old_files(self) -> None:
        """Clean up expired cache files and rate limit data"""
        async with self._rate_limit_cleanup_lock:
            now = datetime.now()
            
            # Only run cleanup if enough time has passed
            if (now - self._last_cleanup).total_seconds() < self.cleanup_interval:
                return
                
            try:
                # Clean up old cache files
                cache_dir = Path("subtitles")
                if cache_dir.exists():
                    for cache_file in cache_dir.glob("*.json"):
                        try:
                            data = json.loads(cache_file.read_text())
                            timestamp = data.get("timestamp", 0)
                            if now.timestamp() - timestamp > self.cache_ttl:
                                cache_file.unlink()
                                # Also remove corresponding .srt file if it exists
                                srt_file = cache_file.with_suffix('.srt')
                                if srt_file.exists():
                                    srt_file.unlink()
                        except Exception as e:
                            print(f"Error cleaning up cache file {cache_file}: {str(e)}")
                            continue
                
                # Clean up old rate limit data
                stale_users = []
                for user, data in self._user_rate_limits.items():
                    if (now - data["last_access"]).total_seconds() > self.cleanup_interval:
                        stale_users.append(user)
                
                for user in stale_users:
                    del self._user_rate_limits[user]
                
                self._last_cleanup = now
            except Exception as e:
                print(f"Cleanup error: {str(e)}")
