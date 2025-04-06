# GeminiTextTransAPI.py
import os
import time
import google.generativeai as genai
from dotenv import load_dotenv
from typing import List, Optional, Callable

load_dotenv()

class GeminiTextTransAPI:
    """
    A class to translate text using the Google Gemini API, mirroring the BaiduTextTransAPI interface.
    """
    def __init__(self, to_lang: str):
        """
        Initializes the GeminiTextTransAPI.

        Args:
            to_lang: The target language code (e.g., 'en', 'zh-CN').
        """
        gemini_api_key = os.getenv("GEMINI_API_KEY")
        if not gemini_api_key:
            raise ValueError("GEMINI_API_KEY environment variable not set.")

        genai.configure(api_key=gemini_api_key) # type: ignore

        # For simplicity, using the gemini-pro model. Adjust if needed.
        # Consider adding model selection as a parameter if necessary.
        self.model = genai.GenerativeModel('gemini-2.0-flash-lite') # type: ignore
        self.to_lang = to_lang
        # Consider adding from_lang if automatic detection is not sufficient or desired.
    def translate_s(self, text: str, callback_progress: Optional[Callable[[int, int], None]] = None) -> str:
        """
        Translates a list of strings by sending them as a single batch to the Gemini API,
        separated by a delimiter.

        Args:
            texts: A list of strings to translate.
            callback_progress: An optional function called once before and once after translation.

        Returns:
            A list of translated strings in the same order as the input.

        Raises:
            Exception: If the translation fails or the number of translated segments doesn't match the input.
        """
        if not text:
            return []

        total_texts = len(text)
        if callback_progress:
            callback_progress(0, total_texts) # Indicate start
        prompt = "You are a professional translator and you are translating a subtitle, please translate the following text to {self.to_lang} and maintain the original format."
        # # Use a delimiter unlikely to be in the text naturally
        prompt = prompt + text
        response = self.model.generate_content(prompt)
        return response.text

        # combined_text = delimiter.join(texts_to_translate)

        # # Modify the prompt for bulk translation
        # prompt = f"Translate the following text segments, separated by \"{delimiter}\", to {self.to_lang}. Maintain the separation format:\n\n{combined_text}"

        # try:
        #     # Single API call for the combined text
        #     # Using the existing translate logic but with the new prompt
        #     raw_translated_block = self.translate(prompt) # Assuming self.translate handles the API call

        #     # Split the result back into segments
        #     translated_segments = raw_translated_block.split(delimiter)

        #     # Check if the number of translated segments matches the number of non-empty input texts
        #     if len(translated_segments) != len(texts_to_translate):
        #         print(f"Warning: Mismatch in translated segments count. Expected {len(texts_to_translate)}, got {len(translated_segments)}. Input: '{combined_text[:100]}...', Output: '{raw_translated_block[:100]}...'")
        #         # Fallback or error handling: Here, we'll try to pad or truncate, but this might be inaccurate.
        #         # A more robust solution might involve retrying or logging detailed errors.
        #         diff = len(texts_to_translate) - len(translated_segments)
        #         if diff > 0:
        #             translated_segments.extend(["[Translation Mismatch]"] * diff)
        #         else:
        #             translated_segments = translated_segments[:len(texts_to_translate)]
        #         # raise ValueError(f"Mismatch in translated segments count. Expected {len(texts_to_translate)}, got {len(translated_segments)}.")

        #     # Reconstruct the full list including placeholders for original empty strings
        #     final_translated_texts = [""] * total_texts
        #     trans_idx = 0
        #     for original_idx in original_indices:
        #          if trans_idx < len(translated_segments):
        #               final_translated_texts[original_idx] = translated_segments[trans_idx].strip()
        #               trans_idx += 1
        #          else:
        #               # This case handles if splitting resulted in fewer segments than expected after the warning.
        #               final_translated_texts[original_idx] = "[Translation Mismatch - Missing Segment]"


        # except Exception as e:
        #      print(f"Failed to translate bulk text: {e}")
        #      # Reraise or return error indicators
        #      raise Exception(f"Bulk translation failed: {e}") from e


        # if callback_progress:
        #     # Indicate completion
        #     callback_progress(total_texts, total_texts)

        # return final_translated_texts

# Example Usage (Optional - for testing)
if __name__ == '__main__':
    load_dotenv()
    # Ensure you have a .env file with GEMINI_API_KEY and set BAIDU_TARGET_LANG
    # or pass the target language directly
    target_language = os.getenv('BAIDU_TARGET_LANG', 'en') # Default to English if not set

    try:
        translator = GeminiTextTransAPI(to_lang=target_language)
        texts_to_translate = [
            "你好，世界！",
            "这是一个测试。",
            "机器学习"
        ]

        def progress_update(current, total):
            print(f"\rTranslating... [{current}/{total}] {int(current/total*100)}%", end='')
            if current == total:
                print() # Newline at the end

        translated = translator.translate_s(texts_to_translate, callback_progress=progress_update)
        print("\nOriginal Texts:", texts_to_translate)
        print(f"Translated Texts ({target_language}):", translated)

        # Test single translation
        single_text = "这是一个单独的句子。"
        print(f"\nTranslating single text: '{single_text}'")
        translated_single = translator.translate(single_text)
        print(f"Translated single text ({target_language}): '{translated_single}'")

    except ValueError as ve:
        print(f"Configuration Error: {ve}")
    except Exception as e:
        print(f"An error occurred during translation: {e}") 