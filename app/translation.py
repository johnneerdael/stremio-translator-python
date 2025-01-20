import google.generativeai as genai
from typing import Optional

class TranslationManager:
    def __init__(self, api_key: str, target_lang: str):
        self.target_lang = target_lang
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-pro')
        
        # Configure response schema for structured output
        self.schema = {
            "type": "object",
            "properties": {
                "translation": {
                    "type": "string",
                    "description": "The translated text"
                }
            },
            "required": ["translation"]
        }

    async def translate_text(self, text: str) -> Optional[str]:
        """Translate text to target language with context"""
        try:
            # Create a prompt that provides context about the translation task
            prompt = f"""You are an expert subtitle translator specializing in translating from English to {self.target_lang}.
            Context: This is a subtitle from a video/movie that needs to be translated while preserving the original meaning and style.
            
            Guidelines:
            - Maintain the natural flow and conversational tone of the dialogue
            - Keep proper names, places, and technical terms unchanged
            - Preserve any special formatting or punctuation
            - Consider the cultural context while translating
            - For dangerous content warnings, translate them appropriately for the target audience
            
            Original English text: {text}
            
            Translate the above text to {self.target_lang}, returning ONLY the translated text without any explanations or notes."""

            # Generate translation with schema and safety settings
            response = self.model.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.3,  # Lower temperature for more consistent translations
                    "candidate_count": 1,
                    "stop_sequences": [],
                    "max_output_tokens": 1024,
                    "top_p": 0.8,
                    "top_k": 40,
                    "response_schema": self.schema
                },
                safety_settings=[
                    {
                        "category": "HARM_CATEGORY_DANGEROUS_CONTENT",
                        "threshold": "BLOCK_NONE"
                    }
                ]
            )

            try:
                # Extract translation from structured response
                result = response.candidates[0].content.parts[0]
                if hasattr(result, 'text'):
                    # Clean up any extra whitespace or formatting
                    translation = result.text.strip()
                    if translation:
                        return translation
                print("No valid translation text in response")
                return None
            except Exception as e:
                print(f"Error extracting translation: {str(e)}")
                return None

        except Exception as e:
            print(f"Translation error: {str(e)}")
            return None
