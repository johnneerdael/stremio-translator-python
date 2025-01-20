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

    def parse_srt(self, srt_content: str) -> List[SubtitleEntry]:
        """Parse SRT content into subtitle entries"""
        entries = []
        current_entry = None
        current_text = []
        
        for line in srt_content.strip().split('\n'):
            line = line.strip()
            
            if not line:  # Empty line indicates end of entry
                if current_entry is not None and current_text:
                    current_entry.text = ' '.join(current_text)
                    entries.append(current_entry)
                    current_entry = None
                    current_text = []
                continue
                
            if current_entry is None:
                try:
                    int(line)  # Entry number, skip
                    current_entry = SubtitleEntry(0, "")
                except ValueError:
                    if '-->' in line:  # Time line
                        start = line.split('-->')[0].strip()
                        h, m, s = start.split(':')
                        s, ms = s.split(',')
                        start_ms = (int(h) * 3600 + int(m) * 60 + int(s)) * 1000 + int(ms)
                        current_entry = SubtitleEntry(start_ms, "")
            else:
                current_text.append(line)
                
        # Handle last entry
        if current_entry is not None and current_text:
            current_entry.text = ' '.join(current_text)
            entries.append(current_entry)
            
        return entries

    async def load_cache(self, cache_path: Path) -> Optional[Dict]:
        """Load translated subtitles from cache if not expired"""
        async with self._cache_lock:
            if not cache_path.exists():
                return None
                
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                
                timestamp = data.get("timestamp", 0)
                now = datetime.now().timestamp()
                
                if now - timestamp > self.cache_ttl:
                    cache_path.unlink()
                    return None
                
                return {"subtitles": data["subtitles"]}
            except json.JSONDecodeError as e:
                print(f"Cache JSON decode error: {str(e)}")
                if cache_path.exists():
                    try:
                        cache_path.unlink()
                    except:
                        pass
                return None
            except Exception as e:
                print(f"Cache error: {str(e)}")
                return None
                
                timestamp = data.get("timestamp", 0)
                now = datetime.now().timestamp()
                
                if now - timestamp > self.cache_ttl:
                    cache_path.unlink()
                    return None
                
                return {"subtitles": data["subtitles"]}
            except json.JSONDecodeError as e:
                print(f"Cache JSON decode error: {str(e)}")
                if cache_path.exists():
                    try:
                        cache_path.unlink()
                    except:
                        pass
                return None
            except Exception as e:
                print(f"Cache error: {str(e)}")
                return None

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
                search_params['parent_imdb_id'] = imdb_id.replace('tt', '')
                if season and episode:
                    search_params['season_number'] = int(season)
                    search_params['episode_number'] = int(episode)
            else:
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
                    sub_filename = subtitle.get('attributes', {}).get('release', '') or subtitle.get('attributes', {}).get('files', [{}])[0].get('file_name', '')
                    
                    if video_filename and sub_filename:
                        clean_video = re.sub(r'[^\w\s]', '', video_filename.lower())
                        clean_sub = re.sub(r'[^\w\s]', '', sub_filename.lower())
                        
                        ratio = SequenceMatcher(None, clean_video, clean_sub).ratio()
                        print(f"Subtitle: {sub_filename}")
                        print(f"Similarity: {ratio * 100:.2f}%")
                        print(f"Foreign parts only: {subtitle.get('attributes', {}).get('foreign_parts_only', False)}")
                        
                        if ratio > best_match_ratio:
                            best_match_ratio = ratio
                            best_subtitle = subtitle

                if not best_subtitle:
                    best_subtitle = max(subtitles, key=lambda s: s.get('attributes', {}).get('download_count', 0))
                    print(f"No filename match found, using most downloaded subtitle")

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
                    
                    async with session.get(download_data['link']) as content_response:
                        if content_response.status != 200:
                            raise Exception(f"Content download failed: {content_response.status}")
                        
                        srt_content = await content_response.text()
                        return self.parse_srt(srt_content)

        except Exception as e:
            print(f"Error fetching subtitles: {str(e)}")
            raise
