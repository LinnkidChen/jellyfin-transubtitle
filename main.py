import base64
import os
import re
import time
from datetime import datetime
from io import StringIO
from typing import List, Dict
import concurrent.futures
from concurrent.futures import ThreadPoolExecutor

import ass
import requests
from dotenv import load_dotenv
from requests import Response

from Baidu_Text_transAPI import BaiduTextTransAPI

load_dotenv()

USER_NAME = os.getenv("USER_NAME")
BASE_URI = os.getenv("BASE_URI")
API_TOKEN = os.getenv("API_TOKEN")
JELLYFIN_TARGET_LANG = os.getenv("JELLYFIN_TARGET_LANG")
SCAN_INTERVAL = int(os.getenv("SCAN_INTERVAL"))
MAX_WORKERS = int(os.getenv("MAX_WORKERS", 8))

btt_api = BaiduTextTransAPI(
    os.getenv('BAIDU_APP_ID'),
    os.getenv('BAIDU_APP_KEY'),
    'auto',
    os.getenv('BAIDU_TARGET_LANG'),
)


def jellyfin(path, method='get', **kwargs) -> 'Response':
    return getattr(requests, method)(
        f'{BASE_URI}/{path}',
        headers={'X-MediaBrowser-Token': API_TOKEN},
        **kwargs
    )


users = jellyfin('Users').json()  # type: List[dict]
users_name_mapping = {user.get('Name'): user for user in users}  # type: Dict[str, dict]
user_id = users_name_mapping.get(USER_NAME).get('Id')


def scan(executor: ThreadPoolExecutor, FOLDER_ID=None, callback=None):
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
    doc = ass.parse_string(content)

    for style in doc.styles:
        style.fontname = 'FZZhengHei-M-GBK'

    tag_strs = []
    texts = []
    for event in doc.events:
        text = event.text
        tags = re.findall(r'\{.+?\}', text)
        tag_str = ''.join(tags)
        for tag in tags:
            text = text.replace(tag, '')
        text = text.replace(r'\N', '\n')
        text = re.sub(r'\s+', ' ', text)

        tag_strs.append(tag_str)
        texts.append(text)

    trans = btt_api.translate_s(
        texts, callback_progress=lambda now, end: print(
            f'\r[{int(now / end * 100)}%] {progress_title}',
            end='' if now < end else '\n'
        ))

    for index, event in enumerate(doc.events):
        event.text = tag_strs[index] + trans[index]

    with StringIO() as io:
        doc.dump_file(io)
        io.seek(0)
        result = io.read()

    return result


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

        ass_streams = list(filter(
            lambda stream: (stream.get('Type') == 'Subtitle' and stream.get('Codec') == 'ass'), streams
        ))

        if not ass_streams:
             return

        langs = set(map(lambda stream: stream.get('Language'), ass_streams))
        if JELLYFIN_TARGET_LANG in langs:
            return

        stream_to_translate = ass_streams[0]
        index = stream_to_translate.get('Index')
        language = stream_to_translate.get('Language', 'und')

        print(datetime.now(), f'- Processing subtitle {index} ({language}) for: {item_name}')

        resp = jellyfin(f'Videos/{item_id}/{item_id}/Subtitles/{index}/Stream.ass')
        resp.raise_for_status()
        content = resp.content.decode('utf-8')

        print(datetime.now(), f'- Translating subtitle {index} ({language} -> {JELLYFIN_TARGET_LANG}) for: {item_name}')
        translated_content = translate_ass(content, progress_title=f"{item_name} ({language} -> {JELLYFIN_TARGET_LANG})")

        print(datetime.now(), f'- Uploading translated ({JELLYFIN_TARGET_LANG}) subtitle for: {item_name}')
        post_resp = jellyfin(f'Videos/{item_id}/Subtitles', method='post', json=dict(
            data=base64.b64encode(translated_content.encode('utf-8')).decode(),
            format='ass',
            isForced=False,
            language=JELLYFIN_TARGET_LANG,
        ))
        post_resp.raise_for_status()
        print(datetime.now(), f'- Successfully translated subtitle for: {item_name}')

    except requests.exceptions.RequestException as e:
        print(datetime.now(), f'- ERROR processing item {item_name} (ID: {item_id}): Network error - {e}')
    # Catch generic Exception for ass library errors as specific exception type is unclear
    # except ass.Error as e: 
    #     print(datetime.now(), f'- ERROR processing item {item_name} (ID: {item_id}): ASS processing error - {e}')
    except Exception as e:
        # Check if the error originates from the ass library processing if needed for specific handling
        # Example: if "ass." in str(type(e)):
        #             handle specifically
        print(datetime.now(), f'- ERROR processing item {item_name} (ID: {item_id}): Processing error - {e.__class__.__name__}: {e}')


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
