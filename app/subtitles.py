import json
from typing import List, Dict, Optional
from pathlib import Path
import asyncio
from datetime import datetime, timedelta
from opensubtitlescom import OpenSubtitles

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
    def __init__(self, api_key: str):
        self.client = OpenSubtitles("Stremio AI Translator v1.6.3", api_key)
        self.batch_size = 15  # Free tier: 15 requests per second
        self.window_size = 60  # 1 minute window
        self.last_batch_time = datetime.now()
        self.requests_in_window = 0
        self.buffer_time = 2 * 60 * 1000  # 2 minutes buffer in milliseconds

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

            # Build search query
            search_params = {
                'languages': 'en',
                'imdb_id': imdb_id.replace('tt', ''),  # OpenSubtitles expects ID without 'tt'
            }
            
            if type == 'series' and season and episode:
                search_params['season_number'] = int(season)
                search_params['episode_number'] = int(episode)

            print(f"OpenSubtitles search params: {json.dumps(search_params, indent=2)}")
            
            # Search for subtitles
            response = self.client.search(**search_params)
            print(f"OpenSubtitles search results: {response.to_json()}")
            
            if not response.data:
                raise Exception("No subtitles found")

            # Get first subtitle
            subtitle = response.data[0]
            
            # Download and parse subtitle
            srt_content = self.client.download_and_parse(subtitle)
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
                start_ms = start_time.total_seconds() * 1000
                
                # Get text
                text = '\n'.join(parts[2:]).strip()
                if text:  # Only add if there's actual text
                    entries.append(SubtitleEntry(int(start_ms), text))
            except Exception as e:
                print(f"Error parsing subtitle entry: {str(e)}")
                continue
        
        return sorted(entries, key=lambda x: x.start)

    def parse_timecode(self, timecode):
        """Parse timecode string into datetime"""
        parts = timecode.split(':')
        hours = int(parts[0])
        minutes = int(parts[1])
        seconds, milliseconds = [int(p) for p in parts[2].split(',')]
        return datetime(1, 1, 1, hours, minutes, seconds, milliseconds * 1000)

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

    async def process_batch(self, batch: List[SubtitleEntry], translate_fn) -> None:
        """Process a batch of subtitles with rate limiting"""
        now = datetime.now()
        
        # Reset counter if window has passed
        if (now - self.last_batch_time) > timedelta(seconds=self.window_size):
            self.requests_in_window = 0
            self.last_batch_time = now

        # Check rate limit
        if self.requests_in_window >= self.batch_size:
            wait_time = self.window_size - (now - self.last_batch_time).seconds
            if wait_time > 0:
                await asyncio.sleep(wait_time)
                self.requests_in_window = 0
                self.last_batch_time = datetime.now()

        # Process batch
        tasks = []
        for entry in batch:
            if not entry.translated_text:  # Only translate if not already translated
                tasks.append(translate_fn(entry.text))
                self.requests_in_window += 1

        translations = await asyncio.gather(*tasks)
        for entry, translation in zip(batch, translations):
            entry.translated_text = translation

    def save_cache(self, entries: List[SubtitleEntry], cache_path: Path) -> None:
        """Save translated subtitles to cache"""
        subtitles = {"subtitles": [entry.to_dict() for entry in entries]}
        cache_path.write_text(json.dumps(subtitles, ensure_ascii=False))

    def load_cache(self, cache_path: Path) -> Optional[Dict]:
        """Load translated subtitles from cache"""
        if cache_path.exists():
            return json.loads(cache_path.read_text())
        return None
