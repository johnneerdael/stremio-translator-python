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
from .subtitles import SubtitleProcessor
from .translation import TranslationManager
from .languages import get_languages, is_language_supported

# Initialize FastAPI
app = FastAPI(debug=True)

# Mount static files
app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "assets"), name="assets")

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
    return {
        "id": "org.stremio.aitranslator",
        "version": "1.6.3",
        "name": "AI Subtitle Translator",
        "description": "Translates subtitles using Google Gemini AI",
        "resources": ["subtitles"],
        "types": ["movie", "series"],
        "catalogs": [],
        "idPrefixes": ["tt"],
        "logo": f"{base_url}/assets/logo.png",
        "background": f"{base_url}/assets/wallpaper.png",
        "behaviorHints": {
            "configurable": True,
            "configurationRequired": True
        },
        "config": [
            {
                "key": "key",
                "type": "password",
                "title": "Google Gemini API Key",
                "required": True
            },
            {
                "key": "lang",
                "type": "select",
                "title": "Target Language",
                "options": [lang["code"] for lang in get_languages()],
                "required": True
            },
            {
                "key": "cache",
                "type": "number",
                "title": "Cache Time (hours)",
                "default": "24"
            },
            {
                "key": "concurrent",
                "type": "number",
                "title": "Max Concurrent Translations",
                "default": "3"
            },
            {
                "key": "debug",
                "type": "checkbox",
                "title": "Debug Mode"
            }
        ]
    }

class Config(BaseModel):
    key: Optional[str] = None
    lang: Optional[str] = None
    cache: Optional[int] = 24
    concurrent: Optional[int] = 3
    debug: Optional[bool] = False
    start_time: Optional[int] = 0  # Start time in milliseconds

async def get_config(config_b64: Optional[str] = None) -> Config:
    """Get configuration from base64 or default values"""
    if config_b64:
        try:
            config_json = base64.b64decode(config_b64).decode()
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
    config = await get_config(config_b64)
    
    if not config.key:
        raise HTTPException(status_code=400, detail="API key not configured")
    
    if not config.lang:
        raise HTTPException(status_code=400, detail="Target language not configured")
    
    # Initialize processors
    subtitle_processor = SubtitleProcessor()
    translation_manager = TranslationManager(config.key, config.lang)
    
    # Check cache
    cache_path = CACHE_DIR / f"{type}-{id}-{video_hash}.json"
    cached = subtitle_processor.load_cache(cache_path)
    if cached:
        return JSONResponse(cached)
    
    try:
        # Fetch subtitles
        entries = await subtitle_processor.fetch_subtitles(type, id)
        
        # Split into priority batches based on start time
        batches = subtitle_processor.prioritize_subtitles(entries, config.start_time or 0)
        
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
            "subtitles": [entry.to_dict() for entry in entries]
        })
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)
