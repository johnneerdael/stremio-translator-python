from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import base64
import json
import os
import asyncio
from pathlib import Path
from typing import Optional, Dict
from pydantic import BaseModel
from urllib.parse import unquote
from .subtitles import SubtitleProcessor
from .translation import TranslationManager
from .languages import get_languages, is_language_supported

# Initialize FastAPI
app = FastAPI(debug=True)

# Mount static files and loading subtitle
app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "assets"), name="assets")

@app.get("/loading.srt")
async def loading_subtitle():
    """Serve the loading subtitle file"""
    loading_path = Path(__file__).parent / "assets" / "loading.srt"
    with open(loading_path, "r", encoding="utf-8") as f:
        return f.read()

# Initialize templates
templates = Jinja2Templates(directory="templates")

# Ensure cache directories exist
CACHE_DIR = Path("subtitles")
CACHE_DIR.mkdir(exist_ok=True)

def get_base_url():
    """Get base URL from environment or default"""
    domain = os.getenv("BASE_DOMAIN", "localhost:7000")
    protocol = "https" if "localhost" not in domain else "http"
    return f"{protocol}://{domain}"

# Manifest definition
def get_manifest(base_url: str):
    domain = base_url.replace("https://", "").replace("http://", "")
    manifest = {
        "id": "org.stremio.aitranslator",
        "version": "1.6.3",
        "name": "AI Subtitle Translator",
        "description": "Translates subtitles using Google Gemini AI",
        "resources": ["subtitles"],
        "types": ["movie", "series"],
        "idPrefixes": ["tt"],
        "catalogs": [],
        "logo": f"{base_url}/assets/logo.png",
        "background": f"{base_url}/assets/wallpaper.png",
        "contactEmail": "john@neerdael.nl"
    }
    return manifest

class Config(BaseModel):
    key: Optional[str] = None  # Gemini API key
    lang: Optional[str] = None
    opensubtitles_key: Optional[str] = None  # OpenSubtitles API key

async def get_config(config_b64: Optional[str] = None) -> Config:
    """Get configuration from base64 or default values"""
    if config_b64:
        try:
            # Add padding if needed
            padding = 4 - (len(config_b64) % 4)
            if padding != 4:
                config_b64 += '=' * padding
            
            config_json = base64.urlsafe_b64decode(config_b64).decode()
            config = Config.parse_raw(config_json)
            if not is_language_supported(config.lang):
                raise ValueError(f"Unsupported language: {config.lang}")
            return config
        except Exception as e:
            print(f"Config error: {e}")
    return Config()

@app.get("/health")
async def health():
    """Health check endpoint"""
    return {"status": "ok"}

@app.get("/")
async def root():
    """Redirect root to configure page"""
    return RedirectResponse(url="/configure")

@app.get("/configure")
async def configure(request: Request):
    """Configuration page"""
    base_url = get_base_url()
    manifest = get_manifest(base_url)
    return templates.TemplateResponse(
        "config.html",
        {
            "request": request,
            "config": Config(),
            "languages": get_languages(),
            "version": manifest["version"],
            "base_url": base_url
        }
    )

@app.get("/{config_b64}/configure")
async def configure_with_config(request: Request, config_b64: str):
    """Configuration page with existing config"""
    config = await get_config(config_b64)
    base_url = get_base_url()
    manifest = get_manifest(base_url)
    return templates.TemplateResponse(
        "config.html",
        {
            "request": request,
            "config": config,
            "languages": get_languages(),
            "version": manifest["version"],
            "base_url": base_url
        }
    )

@app.get("/manifest.json")
@app.get("/{config_b64}/manifest.json")
async def manifest(request: Request, config_b64: Optional[str] = None):
    """Manifest endpoint"""
    base_url = get_base_url()
    manifest_data = get_manifest(base_url)
    return JSONResponse(manifest_data)

@app.get("/{config_b64}/subtitles/{type}/{id}/{video_hash}.json")
async def subtitles(config_b64: str, type: str, id: str, video_hash: str):
    """Subtitle endpoint with smart batching and caching"""
    try:
        # Decode URL parameters
        video_hash = unquote(video_hash).split('.json')[0]  # Remove .json and decode
        if '=' in video_hash:
            # Handle Stremio's hash format: videoHash=123&videoSize=456
            params = dict(param.split('=') for param in video_hash.split('&'))
            video_hash = params.get('videoHash', '')
        
        config = await get_config(config_b64)
        
        if not config.key:
            raise HTTPException(status_code=400, detail="API key not configured")
        
        if not config.lang:
            raise HTTPException(status_code=400, detail="Target language not configured")
            
        if not config.opensubtitles_key:
            # If no API key, check if English subtitles are included in stream
            return JSONResponse({
                "subtitles": [
                    # First try English subtitles from stream
                    {
                        "id": "included",
                        "lang": "eng",
                        "url": None  # This tells Stremio to use embedded subtitles
                    },
                    # Fallback to loading message
                    {
                        "id": "loading",
                        "lang": config.lang,
                        "url": f"{get_base_url()}/loading.srt"
                    }
                ]
            })
        
        # Initialize processors
        subtitle_processor = SubtitleProcessor(config.opensubtitles_key)
        translation_manager = TranslationManager(config.key, config.lang)
        
        # Check cache
        cache_path = CACHE_DIR / f"{type}-{id}-{video_hash}.json"
        cached = subtitle_processor.load_cache(cache_path)
        if cached:
            return JSONResponse(cached)
        
        # Fetch subtitles
        entries = await subtitle_processor.fetch_subtitles(type, id)
        
        # Split into priority batches based on start time
        batches = subtitle_processor.prioritize_subtitles(entries)
        
        # Process first batch immediately (buffer time from start point)
        if batches:
            await subtitle_processor.process_batch(
                batches[0],
                translation_manager.translate_text
            )
            # Save initial cache with first batch
            subtitle_processor.save_cache(entries, cache_path)
        
        # Process remaining batches in background
        if len(batches) > 1:
            async def process_remaining():
                for batch in batches[1:]:
                    await subtitle_processor.process_batch(
                        batch,
                        translation_manager.translate_text
                    )
                    # Update cache after each batch
                    subtitle_processor.save_cache(entries, cache_path)
            
            asyncio.create_task(process_remaining())
        
        # Return current state of subtitles
        return JSONResponse({
            "subtitles": [{
                "id": "translated",
                "lang": config.lang,
                "url": f"{get_base_url()}/subtitles/{type}/{id}/{video_hash}/translated.srt"
            }]
        })
        
    except Exception as e:
        print(f"Subtitle error: {str(e)}")  # Log the error
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)
