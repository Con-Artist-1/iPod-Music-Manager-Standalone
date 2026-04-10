import urllib.request
import zipfile
import os
import shutil

url = "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip"
print("Downloading ffmpeg essentials...")
try:
    urllib.request.urlretrieve(url, "ffmpeg.zip")
    print("Extracting...")
    with zipfile.ZipFile("ffmpeg.zip", 'r') as zf:
        for member in zf.namelist():
            if member.endswith("ffmpeg.exe"):
                extracted = zf.extract(member, path=".")
                shutil.move(extracted, "ffmpeg.exe")
                print("Extracted ffmpeg.exe")
                break
    
    if os.path.exists("ffmpeg.zip"):
        os.remove("ffmpeg.zip")
        
    for name in os.listdir("."):
        if name.startswith("ffmpeg-") and os.path.isdir(name):
            shutil.rmtree(name)
    print("Cleanup complete.")
except Exception as e:
    print(f"Failed: {e}")
