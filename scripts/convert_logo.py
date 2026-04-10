import sys
import subprocess
import os

try:
    from PIL import Image
except ImportError:
    subprocess.check_call([sys.executable, "-m", "pip", "install", "pillow"])
    from PIL import Image

src_png = r"C:\Users\Diavlon\.gemini\antigravity\brain\f0cf1af1-b7c0-41f0-a33d-c9b241091dd7\logo_1775748360024.png"
dst_ico = r"d:\Ipod\logo.ico"

img = Image.open(src_png)
# Optional: Make it square or transparent if needed, usually stable.
img.save(dst_ico, format="ICO", sizes=[(256, 256), (128, 128), (64, 64), (48, 48), (32, 32), (16, 16)])
print(f"Successfully converted {src_png} to {dst_ico}")
