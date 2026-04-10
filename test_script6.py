import os
import sys
sys.path.insert(0, r'd:\Ipod\src')
from voiceover import generate_voiceover_wav
from utils import find_ffmpeg

import voiceover
voiceover._orig_generate_voiceover_wav = voiceover.generate_voiceover_wav

def debug_generate(out_path, text, ffmpeg_path):
    print("Testing text:", text)
    from gtts import gTTS
    has_kana = any('\u3040' <= c <= '\u30ff' for c in text)
    chunks = []
    current_lang = 'en'
    current_text = ""
    for c in text:
        l = 'en'
        if '\u3040' <= c <= '\u30ff': l = 'ja'
        elif '\uac00' <= c <= '\ud7a3': l = 'ko'
        elif '\u4e00' <= c <= '\u9fff': l = 'ja' if has_kana else 'zh-CN'
        elif not c.isalpha(): l = current_lang
        
        if l != current_lang and current_text.strip():
            chunks.append((current_text, current_lang))
            current_text = ""
            current_lang = l
        current_text += c
    if current_text.strip(): chunks.append((current_text, current_lang))
    elif current_text: chunks.append((current_text, 'en'))
        
    mp3_paths = []
    import subprocess
    for i, (chunk_text, chunk_lang) in enumerate(chunks):
        if not any(c.isalnum() for c in chunk_text):
            print("Skipped chunk due to no alnum:", repr(chunk_text))
            continue
        chunk_mp3 = out_path + f"_{i}.mp3"
        try:
            tts = gTTS(text=chunk_text, lang=chunk_lang)
            tts.save(chunk_mp3)
            mp3_paths.append(chunk_mp3)
        except Exception as e:
            print("Failed to save", chunk_text)
            
    print("Valid MP3s generated:", len(mp3_paths))

ffmpeg_path = find_ffmpeg()
debug_generate('test.wav', "[중경삼림 重慶森林 OST] California Dreamin' - The Mamas & The Papas.mp3", ffmpeg_path)