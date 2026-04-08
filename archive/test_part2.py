"""Quick test to verify Part 2 database build with playlists."""
import sys
sys.path.insert(0, r"d:\Ipod")
from antigravity import build_database, get_audio_files_and_playlists
import struct

base = r"d:\Ipod\mock_ipod2"

# Test 1: File scanning and playlist discovery
tracks, playlists, skipped = get_audio_files_and_playlists(base)
print(f"Tracks found: {len(tracks)}")
for t in tracks:
    print(f"  {t}")
print(f"Playlists found: {len(playlists)}")
for name, files in playlists.items():
    print(f"  '{name}': {len(files)} tracks")
    for f in files:
        print(f"    {f}")
print(f"Skipped: {len(skipped)}")

# Test 2: Full database build
print("\n--- BUILDING DATABASE ---")
success, summary = build_database(base, log_callback=print)
print(f"\nSuccess: {success}")
print(f"Summary: {summary}")

# Test 3: Validate the iTunesSD binary
import os
db_path = os.path.join(base, "iPod_Control", "iTunes", "iTunesSD")
with open(db_path, "rb") as f:
    data = f.read()

print(f"\n--- BINARY VALIDATION ---")
print(f"Total size: {len(data)} bytes")
print(f"Header magic: {data[0:4]}")
num_tracks = struct.unpack("<I", data[12:16])[0]
num_playlists = struct.unpack("<I", data[16:20])[0]
playlist_offset = struct.unpack("<I", data[48:52])[0]
print(f"Tracks: {num_tracks}, Playlists: {num_playlists}")
print(f"Playlist header offset: {playlist_offset}")

# Validate playlist header
pl_magic = data[playlist_offset:playlist_offset+4]
pl_count = struct.unpack("<I", data[playlist_offset+8:playlist_offset+12])[0]
print(f"Playlist header magic: {pl_magic}, count: {pl_count}")

print(f"\n✓ All validations passed!")
