import google.generativeai as genai
from typing import Optional, Dict
import asyncio
from functools import lru_cache
import time
from datetime import datetime, timedelta

class TranslationManager:
    def __init__(self, api_key: str, target_lang: str):
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-pro')
        self.target_lang = target_lang
        self._translation_cache: Dict[str, str] = {}
        self._last_request_time = datetime.now()
        self.requests_per_minute = 60  # Free tier limit
        self._request_count = 0
        self._lock = asyncio.Lock()

    @lru_cache(maxsize=1000)
    async def translate_text(self, text: str) -> str:
        """Translate text using Google Gemini AI with caching and rate limiting"""
        if not text.strip():
            return text

        # Check in-memory cache
        cache_key = f"{text}:{self.target_lang}"
        if cache_key in self._translation_cache:
            return self._translation_cache[cache_key]

        async with self._lock:
            try:
                # Check rate limits
                now = datetime.now()
                if (now - self._last_request_time) > timedelta(minutes=1):
                    self._request_count = 0
                    self._last_request_time = now
                elif self._request_count >= self.requests_per_minute:
                    # Wait until next minute if rate limit reached
                    wait_time = 60 - (now - self._last_request_time).seconds
                    if wait_time > 0:
                        await asyncio.sleep(wait_time)
                    self._request_count = 0
                    self._last_request_time = datetime.now()

                # Translate with retry logic
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        prompt = self._create_translation_prompt(text)
                        response = await self.model.generate_content_async(prompt)
                        if not response or not response.text:
                            raise Exception("Empty response from Gemini API")
                        
                        translation = response.text.strip()
                        if not translation:
                            raise Exception("Empty translation")
                        
                        # Cache the result
                        self._translation_cache[cache_key] = translation
                        self._request_count += 1
                        
                        return translation
                    except Exception as e:
                        print(f"Translation error (attempt {attempt + 1}/{max_retries}): {str(e)}")
                        if attempt == max_retries - 1:
                            raise
                        await asyncio.sleep(1 * (attempt + 1))  # Exponential backoff
            except Exception as e:
                print(f"Translation failed: {str(e)}")
                # Return original text if translation fails
                return text

    def _create_translation_prompt(self, text: str) -> str:
        """Create a prompt for translation"""
        return (
            f"Translate the following text to {self.target_lang}. "
            f"Only respond with the translation, no explanations or additional text:\n\n{text}"
        )

    async def translate_batch(self, texts: list[str]) -> list[str]:
        """Translate a batch of texts with rate limiting"""
        tasks = [self.translate_text(text) for text in texts]
        return await asyncio.gather(*tasks)

    def clear_cache(self) -> None:
        """Clear the translation cache"""
        self._translation_cache.clear()
        self.translate_text.cache_clear()
