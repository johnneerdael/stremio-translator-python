import google.generativeai as genai

class TranslationManager:
    def __init__(self, api_key, target_lang):
        genai.configure(api_key=api_key)
        self.target_lang = target_lang
        self.model = genai.GenerativeModel('gemini-pro')

    async def translate_text(self, text):
        try:
            prompt = f"Translate this text to {self.target_lang}. Only return the translation, no explanations: {text}"
            response = self.model.generate_content(prompt)
            return response.text.strip()
        except Exception as e:
            print(f"Translation error: {str(e)}")
            return text  # Return original text if translation fails
