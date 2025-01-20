from google.generativeai import TranslationClient

class TranslationManager:
    def __init__(self, api_key, target_lang):
        self.client = TranslationClient(api_key=api_key)
        self.target_lang = target_lang

    async def translate_text(self, text):
        result = self.client.translate_text(
            text=text,
            target_language=self.target_lang,
            model="nmt"
        )
        return result.translated_text
