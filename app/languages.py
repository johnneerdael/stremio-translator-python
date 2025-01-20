from typing import List, Dict

LANGUAGES = [
    {"code": "ar", "name": "Arabic"},
    {"code": "bn", "name": "Bengali"},
    {"code": "bg", "name": "Bulgarian"},
    {"code": "zh", "name": "Chinese"},
    {"code": "hr", "name": "Croatian"},
    {"code": "cs", "name": "Czech"},
    {"code": "da", "name": "Danish"},
    {"code": "nl", "name": "Nederlands"},
    {"code": "en", "name": "English"},
    {"code": "et", "name": "Estonian"},
    {"code": "fi", "name": "Finnish"},
    {"code": "fr", "name": "French"},
    {"code": "de", "name": "German"},
    {"code": "el", "name": "Greek"},
    {"code": "iw", "name": "Hebrew"},
    {"code": "hi", "name": "Hindi"},
    {"code": "hu", "name": "Hungarian"},
    {"code": "id", "name": "Indonesian"},
    {"code": "it", "name": "Italian"},
    {"code": "ja", "name": "Japanese"},
    {"code": "ko", "name": "Korean"},
    {"code": "lv", "name": "Latvian"},
    {"code": "lt", "name": "Lithuanian"},
    {"code": "no", "name": "Norwegian"},
    {"code": "pl", "name": "Polish"},
    {"code": "pt", "name": "Portuguese"},
    {"code": "ro", "name": "Romanian"},
    {"code": "ru", "name": "Russian"},
    {"code": "sr", "name": "Serbian"},
    {"code": "sk", "name": "Slovak"},
    {"code": "sl", "name": "Slovenian"},
    {"code": "es", "name": "Spanish"},
    {"code": "sw", "name": "Swahili"},
    {"code": "sv", "name": "Swedish"},
    {"code": "th", "name": "Thai"},
    {"code": "tr", "name": "Turkish"},
    {"code": "uk", "name": "Ukrainian"},
    {"code": "vi", "name": "Vietnamese"}
]

def get_languages() -> List[Dict[str, str]]:
    """Get list of supported languages"""
    return LANGUAGES

def is_language_supported(code: str) -> bool:
    """Check if language code is supported"""
    return any(lang["code"] == code for lang in LANGUAGES)

def get_language_name(code: str) -> str:
    """Get language name from code"""
    for lang in LANGUAGES:
        if lang["code"] == code:
            return lang["name"]
    return code
