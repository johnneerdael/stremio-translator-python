import aiohttp
import json
from typing import List, Dict, Optional, Tuple
from pathlib import Path
import asyncio
from datetime import datetime, timedelta

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
    def __init__(self):
        self.stremio_proxy = "https://stremio-opensubtitles.strem.io"
        self.batch_size = 15  # Free tier: 15 requests per second
        self.window_size = 60  # 1 minute window
        self.last_batch_time = datetime.now()
        self.requests_in_window = 0
        self.buffer_time = 2 * 60 * 1000  # 2 minutes buffer in milliseconds

    async def fetch_subtitles(self, type: str, id: str) -> List[SubtitleEntry]:
        """Fetch subtitles from Stremio's OpenSubtitles proxy"""
        async with aiohttp.ClientSession() as session:
            url = f"{self.stremio_proxy}/subtitles/{type}/{id}/en.srt"
            async with session.get(url) as response:
                if response.status != 200:
                    raise Exception(f"Failed to fetch subtitles: {response.status}")
                
                srt_content = await response.text()
                return self.parse_srt(srt_content)

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
            
            # Parse timecode
            times = parts[1].split(' --> ')[0]
            h, m, s = times.split(':')
            ms = s.split(',')[1]
            start_ms = (int(h) * 3600 + int(m) * 60 + int(s.split(',')[0])) * 1000 + int(ms)
            
            # Get text
            text = '\n'.join(parts[2:]).strip()
            entries.append(SubtitleEntry(start_ms, text))
        
        return sorted(entries, key=lambda x: x.start)

    def prioritize_subtitles(self, entries: List[SubtitleEntry], start_time: int = 0) -> List[List[SubtitleEntry]]:
        """Split subtitles into priority batches starting from given time"""
        if not entries:
            return []

        # Find starting point
        start_idx = 0
        for idx, entry in enumerate(entries):
            if entry.start >= start_time:
                start_idx = idx
                break

        # First batch: Buffer time from start point
        first_batch = []
        later_batches = []
        buffer_end = start_time + self.buffer_time

        for entry in entries[start_idx:]:
            if entry.start <= buffer_end:
                first_batch.append(entry)
            else:
                later_batches.append(entry)

        # Add entries before start point to later batches
        if start_idx > 0:
            later_batches.extend(entries[:start_idx])

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
