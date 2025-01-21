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
            entries = await subtitle_processor.fetch_subtitles(type, id)
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
        
    except Exception as e:
        print(f"Subtitle error: {str(e)}")  # Log the error
        raise HTTPException(status_code=500, detail=str(e))

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=7000)
