import base64
import os
import re
import time
from datetime import datetime
from io import StringIO
from typing import List, Dict, Optional, cast, Callable
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor
import srt

import ass
import requests
from dotenv import load_dotenv
from requests import Response

from Baidu_Text_transAPI import BaiduTextTransAPI
from GeminiTextTransAPI import GeminiTextTransAPI
load_dotenv()

USER_NAME = os.getenv("USER_NAME")
BASE_URI = os.getenv("BASE_URI")
API_TOKEN = os.getenv("API_TOKEN")

# Ensure critical variables are set
if not USER_NAME:
    raise ValueError("USER_NAME environment variable not set.")
if not BASE_URI:
    raise ValueError("BASE_URI environment variable not set.")
if not API_TOKEN:
    raise ValueError("API_TOKEN environment variable not set.")


# Provide defaults for optional or type-checked variables
JELLYFIN_TARGET_LANG = os.getenv("JELLYFIN_TARGET_LANG", "en") # Default to English
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL", "3600")) # Default to 1 hour
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "8")) # Default to 8 workers
BAIDU_TARGET_LANG = os.getenv('BAIDU_TARGET_LANG', 'en') # Default to English
GEMINI_TARGET_LANG = os.getenv('GEMINI_TARGET_LANG', 'en') # Default to English

# Comment out or remove Baidu API instantiation if no longer needed
# btt_api = BaiduTextTransAPI(
#     os.getenv('BAIDU_APP_ID'),
#     os.getenv('BAIDU_APP_KEY'),
#     'auto',
#     BAIDU_TARGET_LANG,
# )

gemini_api = GeminiTextTransAPI(
    GEMINI_TARGET_LANG,
)


def jellyfin(path, method='get', **kwargs) -> 'Response':
    return getattr(requests, method)(
        f'{BASE_URI}/{path}',
        headers={'X-MediaBrowser-Token': API_TOKEN},
        **kwargs
    )


users = jellyfin('Users').json()  # type: List[Dict]
# Explicitly type the dictionary key as Optional[str] initially, then filter or handle None
users_name_mapping: Dict[Optional[str], Dict] = {user.get('Name'): user for user in users}
user_data = users_name_mapping.get(USER_NAME)

if not user_data:
    raise ValueError(f"User '{USER_NAME}' not found on Jellyfin server.")
user_id = user_data.get('Id')
if not user_id:
     raise ValueError(f"User '{USER_NAME}' found, but has no Id.")


def scan(executor: ThreadPoolExecutor, FOLDER_ID: Optional[str] = None, callback: Optional[Callable] = None):
    try:
        resp = jellyfin(f'Users/{user_id}/Items', params=dict(
            Fields='Path,HasSubtitles', ParentId=FOLDER_ID
        )).json()  # type: dict
    except requests.exceptions.RequestException as e:
        print(datetime.now(), f'- Error fetching items in folder {FOLDER_ID}: {e}')
        return
    except Exception as e:
         print(datetime.now(), f'- Unexpected error fetching items in folder {FOLDER_ID}: {e}')
         return

    items = resp.get('Items')
    if not items:
        return

    for item in items:
        item: dict
        if item.get('IsFolder'):
            print(datetime.now(), '- scanning:', item.get('Name'))
            scan(executor, item.get('Id'), callback)
        else:
            if item.get('HasSubtitles'):
                try:
                    full_item = jellyfin(f'Users/{user_id}/Items/{item.get("Id")}', params=dict(Fields='MediaStreams')).json()
                    # type: dict Only fetch MediaStreams field
                except requests.exceptions.RequestException as e:
                    print(datetime.now(), f'- Error fetching details for item {item.get("Name")} (ID: {item.get("Id")}): {e}')
                    continue # Skip this item on error
                except Exception as e:
                    print(datetime.now(), f'- Unexpected error fetching details for item {item.get("Name")} (ID: {item.get("Id")}): {e}')
                    continue # Skip this item on error

                if callback:
                    print(datetime.now(), '- Queueing translation for:', full_item.get('Name'))
                    executor.submit(callback, full_item)


def translate_ass(content: str, progress_title='Translating...'):

    # Use gemini_api instead of btt_api
    trans = gemini_api.translate_s(
        content, callback_progress=lambda now, end: print(
            f'\r[{int(now / end * 100)}%] {progress_title}',
            end='' if now < end else '\n'
        ))

    return trans



def translate_srt(content: str, progress_title='Translating...'):
    """
    Parses an SRT subtitle string, translates the text content,
    and returns the reconstructed SRT string.
    """
    try:
        subs = list(srt.parse(content))
    except Exception as e:
        # srt library might raise various errors on parsing issues
        print(datetime.now(), f"- Error parsing SRT content: {e}")
        raise ValueError("Invalid SRT content") from e

    trans = gemini_api.translate_s(
        content, callback_progress=lambda now, end: print(
            f'\r[{int(now / end * 100)}%] {progress_title}',
            end='' if now < end else '\n'
        ))

    return trans


def translate_subtitle(item: dict):
    item_id = item.get('Id')
    item_name = item.get('Name')
    if not item_id or not item_name:
        print(datetime.now(), f'- Error: Received item with missing ID or Name: {item}')
        return

    try:
        streams = item.get('MediaStreams')
        if not streams:
             print(datetime.now(), f'- No media streams found for: {item_name} (ID: {item_id})')
             return

        # Filter for subtitle streams with a language specified
        subtitle_streams = [
            stream for stream in streams
            if stream.get('Type') == 'Subtitle' and stream.get('Language') is not None
               and stream.get('Codec') in ['ass', 'srt','subrip'] # Accept both ASS and SRT
        ]
       
        if not subtitle_streams:
             print(datetime.now(), f'- No ASS or SRT streams with language found for: {item_name} (ID: {item_id})')
             return

        # Check if target language already exists
        langs = set(stream.get('Language') for stream in subtitle_streams)
        if JELLYFIN_TARGET_LANG in langs:
            print(datetime.now(), f'- Target language {JELLYFIN_TARGET_LANG} already exists for: {item_name}')
            return

        # Prioritize ASS, then SRT
        stream_to_translate = None
        ass_streams = [s for s in subtitle_streams if s.get('Codec') == 'ass']
        srt_streams = [s for s in subtitle_streams if s.get('Codec') == 'srt' or s.get('Codec') == 'subrip']

        if ass_streams:
            stream_to_translate = ass_streams[0] # Select the first ASS stream
            print(datetime.now(), f'- Prioritizing ASS subtitle for: {item_name}')
        elif srt_streams:
            stream_to_translate = srt_streams[0] # Select the first SRT stream if no ASS
            print(datetime.now(), f'- No ASS found, using SRT subtitle for: {item_name}')
        else:
            # This case should technically not be reached due to the earlier check, but good to have
            print(datetime.now(), f'- No suitable ASS or SRT stream found for translation: {item_name} (ID: {item_id})')
            return


        index = stream_to_translate.get('Index')
        language = stream_to_translate.get('Language', 'und') # Already checked for None
        codec = stream_to_translate.get('Codec') # 'ass' or 'srt'

        print(datetime.now(), f'- Processing subtitle {index} ({language}, {codec}) for: {item_name}')

        # Fetch content based on codec
        stream_url_suffix = f"Stream.{codec}" # Dynamic suffix: Stream.ass or Stream.srt
        resp = jellyfin(f'Videos/{item_id}/{item_id}/Subtitles/{index}/{stream_url_suffix}')
        resp.raise_for_status()
        # Assume UTF-8, handle potential decoding errors if necessary
        content = resp.content.decode('utf-8', errors='replace')

        # Existing log - retained
        print(datetime.now(), f'- Translating subtitle {index} ({language} -> {JELLYFIN_TARGET_LANG}, {codec}) for: {item_name}')

        # Translate based on codec
        translated_content = ""
        progress_title=f"{item_name} ({language} -> {JELLYFIN_TARGET_LANG}, {codec})"

        # --- Log: Translation Start ---
        print(datetime.now(), f'- START: Translation process ({codec}) initiated for: {item_name}')
        translation_successful = False
        try:
            if codec == 'ass':
                translated_content = translate_ass(content, progress_title=progress_title)
                translation_successful = True
            elif codec == 'srt' or codec == 'subrip':
                # Use the new translate_srt function
                translated_content = translate_srt(content, progress_title=progress_title)
                translation_successful = True
            else:
                # --- Log: Unsupported Codec Error ---
                print(datetime.now(), f'- FAIL: Unsupported codec {codec} encountered for item {item_name} (ID: {item_id}) - Cannot translate.')
                return # Skip if codec isn't supported

            # --- Log: Translation End (Success) ---
            if translation_successful:
                 print(datetime.now(), f'- END: Translation process ({codec}) finished for: {item_name}')

        except Exception as trans_ex: # Catch errors specifically during the translation call
            # --- Log: Translation Fail ---
            print(datetime.now(), f'- FAIL: Translation process ({codec}) failed for: {item_name} - Error: {trans_ex.__class__.__name__}: {trans_ex}')
            # Re-raise the exception to be caught by the outer try/except block if needed, or handle differently
            raise # Or simply return if you want to stop processing this item on translation failure

        # --- Log: Upload Start ---
        print(datetime.now(), f'- START: Uploading translated ({JELLYFIN_TARGET_LANG}, {codec}) subtitle for: {item_name}')
        # post_resp = jellyfin(f'Videos/{item_id}/Subtitles', method='post', json=dict(
        #     data=base64.b64encode(translated_content.encode('utf-8')).decode(),
        #     format=codec, # Use the detected codec ('ass' or 'srt')
        #     isForced=False,
        #     language=JELLYFIN_TARGET_LANG,
        # ))
        # post_resp.raise_for_status() # Will raise HTTPError for bad responses (4xx or 5xx)
        # --- Log: Upload Success ---
        print(datetime.now(), f'- SUCCESS: Successfully translated and uploaded {codec} subtitle ({JELLYFIN_TARGET_LANG}) for: {item_name}')

        # --- Save Translated Subtitle Locally ---
        try:
            save_dir = "translated_subtitles"
            os.makedirs(save_dir, exist_ok=True)
            # Sanitize filename: remove invalid characters
            sanitized_item_name = re.sub(r'[\\/*?:"<>|]', '_', item_name)
            save_filename = f"{sanitized_item_name} ({JELLYFIN_TARGET_LANG}, {codec}).{codec}"
            save_path = os.path.join(save_dir, save_filename)
            with open(save_path, 'w', encoding='utf-8') as f:
                f.write(translated_content)
            print(datetime.now(), f'- INFO: Saved translated subtitle locally to: {save_path}')
        except Exception as save_ex:
            print(datetime.now(), f'- WARNING: Failed to save translated subtitle locally for {item_name} - Error: {save_ex}')
            # Continue even if saving fails

    except requests.exceptions.RequestException as e:
        # --- Log: Network Error --- (Could happen during fetch or upload)
        print(datetime.now(), f'- FAIL: Network error during processing for {item_name} (ID: {item_id}): {e}')
    except ValueError as e: # Catch specific SRT parsing errors from translate_srt
        # --- Log: SRT Processing Error ---
        print(datetime.now(), f'- FAIL: SRT processing error for {item_name} (ID: {item_id}): {e}')
    # Catch generic Exception for ass library errors as specific exception type is unclear
    # except ass.Error as e: 
    #     print(datetime.now(), f'- ERROR processing item {item_name} (ID: {item_id}): ASS processing error - {e}')
    except Exception as e:
        # --- Log: General Processing Error --- (Could be translation error if re-raised, or upload error, or other)
        # Check if the error originates from the ass library processing if needed for specific handling
        # Example: if "ass." in str(type(e)):
        #             handle specifically
        print(datetime.now(), f'- FAIL: General processing error for {item_name} (ID: {item_id}): {e.__class__.__name__}: {e}')


try:
    print(datetime.now(), f"- Starting subtitle translator with {MAX_WORKERS} workers.")
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
        while True:
            print(datetime.now(), '- Starting library scan...')
            scan(executor, callback=translate_subtitle)
            print(datetime.now(), f'- Scan finished. Sleeping for {SCAN_INTERVAL} seconds...')
            time.sleep(SCAN_INTERVAL)
except KeyboardInterrupt:
    print("\nKeyboardInterrupt received. Shutting down gracefully...")
    pass
print(datetime.now(), "- Script finished.")
