import google.generativeai as genai
import json
from typing import Optional

class TranslationManager:
    def __init__(self, api_key: str, target_lang: str):
        self.target_lang = target_lang
        genai.configure(api_key=api_key)
        self.model = genai.GenerativeModel('gemini-1.5-pro')
        
        # Configure generation settings
        self.generation_config = {
            "temperature": 0.3,
            "candidate_count": 1,
            "stop_sequences": [],
            "max_output_tokens": 1024,
            "top_p": 0.8,
            "top_k": 40
        }
        
    async def translate_text(self, text: str) -> Optional[str]:
        """Translate text to target language with context"""
        try:
            # Parse input SRT format
            lines = text.strip().split('\n')
            if len(lines) < 3:
                print("Invalid SRT format")
                return None
                
            index = int(lines[0])
            timecodes = lines[1].split(' --> ')
            start_time = timecodes[0]
            end_time = timecodes[1]
            subtitle_text = '\n'.join(lines[2:]).strip()
            
            # Create prompt with structured output example
            prompt = f"""You are an expert subtitle translator specializing in translating from English to {self.target_lang}.
            
            Guidelines:
            - Maintain the natural flow and conversational tone of the dialogue
            - Keep proper names, places, and technical terms unchanged
            - Consider the cultural context while translating
            - For dangerous content warnings, translate them appropriately
            
            Translate this subtitle to {self.target_lang} and return in this JSON format:
            {{
                "translation": {{
                    "text": "translated text here",
                    "index": {index},
                    "start_time": "{start_time}",
                    "end_time": "{end_time}"
                }}
            }}
            
            Original English text: {subtitle_text}"""

            # Generate translation
            response = self.model.generate_content(
                prompt,
                generation_config=self.generation_config
            )

            try:
                # Parse JSON response
                result = response.text.strip()
                data = json.loads(result)
                
                # Format as SRT
                srt = f"{data['translation']['index']}\n"
                srt += f"{data['translation']['start_time']} --> {data['translation']['end_time']}\n"
                srt += f"{data['translation']['text']}"
                return srt
                
            except Exception as e:
                print(f"Error parsing translation response: {str(e)}")
                print(f"Raw response: {response.text}")
                return None

        except Exception as e:
            print(f"Translation error: {str(e)}")
            return None
