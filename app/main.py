from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import RedirectResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
import base64
import json
import os
import asyncio
from pathlib import Path
from typing import Optional, Dict
from pydantic import BaseModel
from urllib.parse import unquote, quote
from .subtitles import SubtitleProcessor
from .translation import TranslationManager
from .languages import get_languages, is_language_supported, get_language_name

# Initialize FastAPI
app = FastAPI(debug=True)

# Mount static files and loading subtitle
app.mount("/assets", StaticFiles(directory=Path(__file__).parent / "assets"), name="assets")

@app.get("/loading.srt")
async def loading_subtitle():
    """Serve the loading subtitle file"""
    loading_path = Path(__file__).parent / "assets" / "loading.srt"
    with open(loading_path, "r", encoding="utf-8") as f:
        return Response(
            content=f.read(),
            media_type="application/x-subrip",
            headers={"Content-Disposition": "attachment; filename=loading.srt"}
        )

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
    opensubtitles_app: Optional[str] = None  # OpenSubtitles app name

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
@app.get("/{config_b64}/subtitles/{cache_key}/translated.srt")
async def subtitles(
    config_b64: str,
    type: str = None,
    id: str = None,
    video_hash: str = None,
    cache_key: str = None
):
    """Subtitle endpoint with smart caching and reuse"""
    try:
        config = await get_config(config_b64)
        
        # Handle translated.srt request
        if cache_key:
            # Convert URL-encoded cache key back to filesystem-safe format
            fs_cache_key = cache_key.replace('%3A', ':')
            srt_path = CACHE_DIR / f"{fs_cache_key}.srt"
            if not srt_path.exists():
                raise HTTPException(status_code=404, detail="Subtitle not found")
            return Response(
                content=srt_path.read_text(),
                media_type="application/x-subrip",
                headers={"Content-Disposition": f"attachment; filename={fs_cache_key}.srt"}
            )

        # Handle subtitle list request
        video_hash = unquote(video_hash).split('.json')[0]  # Remove .json and decode
        if '=' in video_hash:
            # Handle Stremio's hash format: videoHash=123&videoSize=456&filename=show.mp4
            params = dict(param.split('=') for param in video_hash.split('&'))
            video_hash = params.get('videoHash', '')
            video_filename = params.get('filename', '')
            if video_filename:
                # Pass filename in the id for subtitle matching
                id = f"{id}&filename={video_filename}"
        
        if not config.key:
            raise HTTPException(status_code=400, detail="API key not configured")
        
        if not config.lang:
            raise HTTPException(status_code=400, detail="Target language not configured")
            
        # Initialize response subtitles list
        response_subtitles = []
        
        # Extract stream metadata
        stream_info = {}
        if '=' in video_hash:
            params = dict(param.split('=') for p in video_hash.split('&'))
            stream_info = {
                'filename': params.get('filename', ''),
                'videoHash': params.get('videoHash', ''),
                'videoSize': params.get('videoSize', '')
            }
            
            if stream_info['filename']:
                print(f"Stream metadata:")
                print(f"- Filename: {stream_info['filename']}")
                print(f"- Video hash: {stream_info['videoHash']}")
                print(f"- Video size: {stream_info['videoSize']}")

        # Check for embedded subtitles
        print("Checking for English subtitles in stream...")
        print(f"Stream metadata: {json.dumps(stream_info, indent=2)}")
        
        # Check if stream has embedded subtitles
        has_embedded = False
        if stream_info.get('filename'):
            # Look for common subtitle indicators in filename
            filename = stream_info['filename'].lower()
            has_embedded = any(x in filename for x in ['.srt', 'sub', 'dubbed', 'multi'])
            print(f"Checking filename '{filename}' for subtitle indicators: {has_embedded}")
            
            if has_embedded:
                print("Stream has embedded English subtitles, adding as primary option")
                response_subtitles.append({
                    "id": "eng-embedded",  # Unique identifier
                    "lang": "eng",         # ISO 639-2 code
                    "url": None            # Null URL for embedded subtitles
                })

        if not config.opensubtitles_key:
            print("No OpenSubtitles API key configured")
            if not response_subtitles:
                print("No embedded subtitles found, showing loading message")
                response_subtitles.append({
                    "id": "loading",
                    "lang": config.lang,  # Keep language code for loading message
                    "url": f"{get_base_url()}/loading.srt"
                })
            return JSONResponse({"subtitles": response_subtitles})
        
        # Initialize processors
        subtitle_processor = SubtitleProcessor(
            api_key=config.opensubtitles_key,
            app_name=config.opensubtitles_app or "Stremio AI Translator"
        )
        translation_manager = TranslationManager(config.key, config.lang)
        
        # Create cache key that includes user-specific data
        base_id = id.split('&')[0]  # Remove filename from ID
        fs_cache_key = f"{config_b64}-{type}-{base_id}"  # Include config in cache key
        url_cache_key = quote(fs_cache_key)  # URL-encoded format
        
        cache_path = CACHE_DIR / f"{fs_cache_key}.json"
        cached = await subtitle_processor.load_cache(cache_path)
        if cached:
            return JSONResponse(cached)
        
        # Parse video ID
        imdb_id, season, episode = None, None, None
        if type == 'series' and ':' in id:
            parts = id.split(':')
            imdb_id = parts[0]
            if len(parts) > 2:
                season = parts[1]
                episode = parts[2]
        else:
            imdb_id = id
        
        # Fetch subtitles
        entries = await subtitle_processor.fetch_subtitles(type, imdb_id, season, episode)
        
        # Define subtitles handler
        subtitles = []

        # Check for embedded subtitles
        if stream_info.get('filename'):
            filename = stream_info['filename'].lower()
            has_embedded = any(x in filename for x in ['.srt', 'sub', 'dubbed', 'multi'])
            if has_embedded:
                subtitles.append({
                    "id": "eng-embedded",
                    "lang": "eng",
                    "url": None
                })

        # Fetch subtitles from OpenSubtitles
        if config.opensubtitles_key:
            for entry in entries:
                subtitles.append({
                    "id": f"{entry.start}-{config.lang}",
                    "lang": config.lang,
                    "url": f"{get_base_url()}/{config_b64}/subtitles/{url_cache_key}/translated.srt#{entry.start}"
                })

        # Add loading message if no subtitles found
        if not subtitles:
            subtitles.append({
                "id": "loading",
                "lang": config.lang,
                "url": f"{get_base_url()}/loading.srt"
            })

        return JSONResponse({"subtitles": subtitles})
        
    except Exception as e:
        print(f"Subtitle error: {str(e)}")  # Log the error
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)
