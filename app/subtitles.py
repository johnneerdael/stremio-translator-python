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
                times = parts[1].split(' --> ')
                start_time, end_time = [self.parse_timecode(t) for t in times]
                start_ms = int(start_time.total_seconds() * 1000)
                
                text = '\n'.join(parts[2:]).strip()
                if text:
                    entries.append(SubtitleEntry(int(start_ms), text))
            except Exception as e:
                print(f"Error parsing subtitle entry: {str(e)}")
                print(f"Block content: {block}")
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

        first_batch = []
        later_batches = []
        two_minutes = 2 * 60 * 1000  # 2 minutes in milliseconds

        for entry in entries:
            if entry.start <= two_minutes:
                first_batch.append(entry)
            else:
                later_batches.append(entry)

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

    def transform_srt_to_structured_data(self, srt_content: str) -> List[Dict]:
        """Transform SRT content into structured data for Gemini."""
        structured_data = []
        entries = self.parse_srt(srt_content)
        
        for entry in entries:
            structured_data.append({
                "index": entry.start,
                "text": entry.text,
                "start_time": entry.start,
                "end_time": entry.start + 2000  # Example duration of 2 seconds
            })
        
        return structured_data

    def transform_structured_data_to_srt(self, structured_data: List[Dict]) -> str:
        """Convert structured data back to valid SRT format."""
        srt_content = []
        
        for item in structured_data:
            srt_content.append(f"{item['index']}")
            srt_content.append(f"{self.format_time(item['start_time'])} --> {self.format_time(item['end_time'])}")
            srt_content.append(item['text'])
            srt_content.append("")  # Blank line between entries
        
        return "\n".join(srt_content).strip()

    def format_time(self, milliseconds: int) -> str:
        """Format milliseconds into SRT timecode format."""
        seconds, ms = divmod(milliseconds, 1000)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"

    def transform_srt_to_structured_data(self, srt_content: str) -> List[Dict]:
        """Transform SRT content into structured data for Gemini."""
        structured_data = []
        entries = self.parse_srt(srt_content)
        
        for entry in entries:
            structured_data.append({
                "index": entry.start,
                "text": entry.text,
                "start_time": entry.start,
                "end_time": entry.start + 2000  # Example duration of 2 seconds
            })
        
        return structured_data

    def transform_structured_data_to_srt(self, structured_data: List[Dict]) -> str:
        """Convert structured data back to valid SRT format."""
        srt_content = []
        
        for item in structured_data:
            srt_content.append(f"{item['index']}")
            srt_content.append(f"{self.format_time(item['start_time'])} --> {self.format_time(item['end_time'])}")
            srt_content.append(item['text'])
            srt_content.append("")  # Blank line between entries
        
        return "\n".join(srt_content).strip()

    def format_time(self, milliseconds: int) -> str:
        """Format milliseconds into SRT timecode format."""
        seconds, ms = divmod(milliseconds, 1000)
        hours, seconds = divmod(seconds, 3600)
        minutes, seconds = divmod(seconds, 60)
        return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"

    async def process_batch(self, batch: List[SubtitleEntry], translate_fn, config_b64: str) -> None:
        """Process a batch of subtitles with user-specific rate limiting"""
        now = datetime.now()
        
        last_batch_time, requests_in_window = self._get_user_rate_limit(config_b64)
        
        if (now - last_batch_time) > timedelta(seconds=self.window_size):
            requests_in_window = 0
            last_batch_time = now

        if requests_in_window >= self.batch_size:
            wait_time = self.window_size - (now - last_batch_time).seconds
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                requests_in_window = 0
                last_batch_time = datetime.now()

        # Buffering fast for initial requests
        initial_requests = min(len(batch), self.batch_size)
        tasks = [translate_fn(entry.text) for entry in batch[:initial_requests]]
        requests_in_window += initial_requests

        # Log original SRT content
        original_srt = "\n".join(entry.text for entry in batch)
        print("Original SRT content:", original_srt)

        # Update user rate limit data
        self._update_user_rate_limit(config_b64, last_batch_time, requests_in_window)

        translations = await asyncio.gather(*tasks)
        for entry, translation in zip(batch[:initial_requests], translations):
            entry.translated_text = translation

        # Log structured data sent to Gemini
        structured_data = self.transform_srt_to_structured_data(original_srt)
        print("Structured data sent to Gemini:", structured_data)

        # Process remaining entries in larger batches
        remaining_entries = batch[initial_requests:]
        while remaining_entries:
            current_batch = remaining_entries[:self.batch_size]
            remaining_entries = remaining_entries[self.batch_size:]

            # Wait if rate limit is reached
            if requests_in_window >= self.batch_size:
                wait_time = self.window_size - (now - last_batch_time).seconds
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                    requests_in_window = 0
                    last_batch_time = datetime.now()

            tasks = [translate_fn(entry.text) for entry in current_batch]
            requests_in_window += len(current_batch)

            # Update user rate limit data
            self._update_user_rate_limit(config_b64, last_batch_time, requests_in_window)

            translations = await asyncio.gather(*tasks)
            for entry, translation in zip(current_batch, translations):
                entry.translated_text = translation

        # Log structured data received from Gemini
        received_structured_data = [entry.translated_text for entry in batch]
        print("Structured data received from Gemini:", received_structured_data)

        # Convert structured data back to SRT format
        converted_srt = self.transform_structured_data_to_srt(structured_data)
        print("Converted SRT content:", converted_srt)
        """Process a batch of subtitles with user-specific rate limiting"""
        now = datetime.now()
        
        last_batch_time, requests_in_window = self._get_user_rate_limit(config_b64)
        
        if (now - last_batch_time) > timedelta(seconds=self.window_size):
            requests_in_window = 0
            last_batch_time = now

        if requests_in_window >= self.batch_size:
            wait_time = self.window_size - (now - last_batch_time).seconds
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                requests_in_window = 0
                last_batch_time = datetime.now()

        # Buffering fast for initial requests
        initial_requests = min(len(batch), self.batch_size)
        tasks = [translate_fn(entry.text) for entry in batch[:initial_requests]]
        requests_in_window += initial_requests

        # Update user rate limit data
        self._update_user_rate_limit(config_b64, last_batch_time, requests_in_window)

        translations = await asyncio.gather(*tasks)
        for entry, translation in zip(batch[:initial_requests], translations):
            entry.translated_text = translation

        # Process remaining entries in larger batches
        remaining_entries = batch[initial_requests:]
        while remaining_entries:
            current_batch = remaining_entries[:self.batch_size]
            remaining_entries = remaining_entries[self.batch_size:]

            # Wait if rate limit is reached
            if requests_in_window >= self.batch_size:
                wait_time = self.window_size - (now - last_batch_time).seconds
                if wait_time > 0:
                    await asyncio.sleep(wait_time)
                    requests_in_window = 0
                    last_batch_time = datetime.now()

            tasks = [translate_fn(entry.text) for entry in current_batch]
            requests_in_window += len(current_batch)

            # Update user rate limit data
            self._update_user_rate_limit(config_b64, last_batch_time, requests_in_window)

            translations = await asyncio.gather(*tasks)
            for entry, translation in zip(current_batch, translations):
                entry.translated_text = translation
        """Process a batch of subtitles with user-specific rate limiting"""
        now = datetime.now()
        
        last_batch_time, requests_in_window = self._get_user_rate_limit(config_b64)
        
        if (now - last_batch_time) > timedelta(seconds=self.window_size):
            requests_in_window = 0
            last_batch_time = now

        if requests_in_window >= self.batch_size:
            wait_time = self.window_size - (now - last_batch_time).seconds
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                requests_in_window = 0
                last_batch_time = datetime.now()

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
