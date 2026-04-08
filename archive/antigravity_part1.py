import os
import sys
import struct
import collections
import hashlib
import time

__version__ = "2.0 (4G)"
__title__ = "Antigravity iPod Manager"

AUDIO_EXTENSIONS = (".mp3", ".m4a", ".m4b", ".m4p", ".aa", ".wav")

def print_banner():
    print("=" * 60)
    print(f" {__title__} v{__version__}".center(60))
    print(" Standalone Zero-Dependency Shuffle Database Builder C.4G".center(60))
    print("=" * 60)
    print()

def progress_bar(iteration, total, prefix='', suffix='', length=40, fill='█', track_name=''):
    if total == 0:
        return
    percent = ("{0:.1f}").format(100 * (iteration / float(total)))
    filled_length = int(length * iteration // total)
    bar = fill * filled_length + '-' * (length - filled_length)
    if len(track_name) > 25:
        track_name = track_name[:22] + "..."
    # Carriage return to overwrite the same line
    sys.stdout.write(f'\r{prefix} |{bar}| {percent}% {suffix} [{track_name.ljust(25)}]')
    sys.stdout.flush()
    if iteration == total: 
        print()
        
class Record(object):
    def __init__(self):
        self._struct = collections.OrderedDict()
        self._fields = {}
        
    def __getitem__(self, item):
        if item not in self._struct:
            raise KeyError
        return self._fields.get(item, self._struct[item][1])

    def __setitem__(self, item, value):
        self._fields[item] = value

    def construct(self):
        output = b""
        for i in self._struct.keys():
            fmt, default = self._struct[i]
            val = self._fields.get(i, default)
            output += struct.pack("<" + fmt, val)
        return output

class TunesSD(Record):
    def __init__(self, num_tracks):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id", ("4s", b"bdhs")),
            ("unknown1", ("I", 0x02000003)),
            ("total_length", ("I", 64)),
            ("total_number_of_tracks", ("I", num_tracks)),
            ("total_number_of_playlists", ("I", 1)),
            ("unknown2", ("Q", 0)),
            ("max_volume", ("B", 0)),
            ("voiceover_enabled", ("B", 0)),
            ("unknown3", ("H", 0)),
            ("total_tracks_without_podcasts", ("I", num_tracks)),
            ("track_header_offset", ("I", 64)),
            ("playlist_header_offset", ("I", 0)), # To be provided later
            ("unknown4", ("20s", b"\x00" * 20)),
        ])

class TrackHeader(Record):
    def __init__(self, num_tracks):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id", ("4s", b"hths")),
            ("total_length", ("I", 20 + num_tracks * 4)),
            ("number_of_tracks", ("I", num_tracks)),
            ("unknown1", ("Q", 0)),
        ])

class Track(Record):
    def __init__(self):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id", ("4s", b"rths")),
            ("header_length", ("I", 0x174)),
            ("start_at_pos_ms", ("I", 0)),
            ("stop_at_pos_ms", ("I", 0)),
            ("volume_gain", ("I", 0)),
            ("filetype", ("I", 1)),
            ("filename", ("256s", b"\x00" * 256)),
            ("bookmark", ("I", 0)),
            ("dontskip", ("B", 1)),
            ("remember", ("B", 0)),
            ("unintalbum", ("B", 0)),
            ("unknown", ("B", 0)),
            ("pregap", ("I", 0x200)),
            ("postgap", ("I", 0x200)),
            ("numsamples", ("I", 0)),
            ("unknown2", ("I", 0)),
            ("gapless", ("I", 0)),
            ("unknown3", ("I", 0)),
            ("albumid", ("I", 0)),
            ("track", ("H", 1)),
            ("disc", ("H", 0)),
            ("unknown4", ("Q", 0)),
            ("dbid", ("8s", b"\x00" * 8)),
            ("artistid", ("I", 0)),
            ("unknown5", ("32s", b"\x00" * 32)),
        ])
    
    def populate(self, full_path, base_path):
        rel_path = os.path.relpath(full_path, base_path)
        ipod_path = "/" + rel_path.replace("\\", "/")
        
        self["filename"] = ipod_path.encode('utf-8')
        
        ext = os.path.splitext(full_path)[1].lower()
        if ext in (".m4a", ".m4b", ".m4p", ".aa"):
            self["filetype"] = 2
            if ext in (".m4b", ".aa"):
                self["dontskip"] = 0
                self["bookmark"] = 1
                self["remember"] = 1
                
        text = os.path.splitext(os.path.basename(full_path))[0]
        text_bytes = text.encode('utf-8', 'ignore')
        self["dbid"] = hashlib.md5(text_bytes).digest()[:8]

class PlaylistHeader(Record):
    def __init__(self):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id", ("4s", b"hphs")),
            ("total_length", ("I", 0x14 + 1 * 4)), # 24 bytes
            ("number_of_playlists", ("I", 1)),
            ("number_of_non_podcast_lists", ("2s", b"\xFF\xFF")),
            ("number_of_master_lists", ("2s", b"\x01\x00")),
            ("number_of_non_audiobook_lists", ("2s", b"\xFF\xFF")),
            ("unknown2", ("2s", b"\x00" * 2)),
        ])

class Playlist(Record):
    def __init__(self, num_tracks):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id", ("4s", b"lphs")),
            ("total_length", ("I", 44 + 4 * num_tracks)),
            ("number_of_songs", ("I", num_tracks)),
            ("number_of_nonaudio", ("I", num_tracks)),
            ("dbid", ("8s", b"\x00" * 8)),
            ("listtype", ("I", 1)),
            ("unknown1", ("16s", b"\x00" * 16))
        ])

def get_audio_files(base_path):
    valid_files = []
    skipped_files = []
    
    scan_dirs = [os.path.join(base_path, "Music"), os.path.join(base_path, "iPod_Control", "Music")]
    
    for search_dir in scan_dirs:
        if not os.path.isdir(search_dir):
            continue
        for root, _, files in os.walk(search_dir):
            for file in files:
                if file.startswith('.'):
                    continue
                ext = os.path.splitext(file)[1].lower()
                filepath = os.path.join(root, file)
                
                if ext in AUDIO_EXTENSIONS:
                    valid_files.append(filepath)
                else:
                    skipped_files.append(filepath)
                    
    valid_files.sort(key=lambda x: os.path.basename(x).lower())
    return valid_files, skipped_files

def main():
    print_banner()
    if getattr(sys, 'frozen', False):
        base_path = os.path.dirname(sys.executable)
    else:
        base_path = os.getcwd()
        
    itunes_dir = os.path.join(base_path, "iPod_Control", "iTunes")
    os.makedirs(itunes_dir, exist_ok=True)
    
    # Remove old iTunes databases to prevent out-of-sync conflicts
    for conflict_file in ["iTunesDB", "iTunesPrefs", "iTunesPrefs.plist", "iTunesControl", "iTunesStats", "iTunesPState"]:
        conflict_path = os.path.join(itunes_dir, conflict_file)
        if os.path.exists(conflict_path):
            try:
                os.remove(conflict_path)
            except:
                pass
                
    files, skipped = get_audio_files(base_path)
    
    if not files:
        print(" [!] No playable audio files found in 'Music/' or 'iPod_Control/Music/'.")
        print("     Make sure you run this ON the iPod, next to your Music directory.\n")
        time.sleep(2)
        sys.exit(0)
        
    num_tracks = len(files)
    
    tunessd = TunesSD(num_tracks)
    
    track_header = TrackHeader(num_tracks)
    track_header_offset = 64
    
    tracks_chunk = b""
    track_header_bytes = track_header.construct()
    
    print(f"Indexing {num_tracks} tracks internally...")
    progress_bar(0, num_tracks, prefix='Building:', suffix='Complete', length=30)
        
    for i, file_path in enumerate(files):
        track = Track()
        track.populate(file_path, base_path)
        
        # Track Header Total Length = 20 + num_tracks * 4
        # Pointer to the start of this specific track block
        ptr_offset = track_header_offset + 20 + (num_tracks * 4) + len(tracks_chunk)
        track_header_bytes += struct.pack("<I", ptr_offset)
        tracks_chunk += track.construct()
        
        progress_bar(i + 1, num_tracks, prefix='Building:', suffix='Complete', length=30, track_name=os.path.basename(file_path))
        time.sleep(0.005)
        
    full_track_segment = track_header_bytes + tracks_chunk
    
    playlist_header_offset = track_header_offset + len(full_track_segment)
    tunessd["playlist_header_offset"] = playlist_header_offset
    
    play_header = PlaylistHeader()
    playlist = Playlist(num_tracks)
    
    play_header_bytes = play_header.construct()
    
    playlist_chunk = playlist.construct()
    for i in range(num_tracks):
        playlist_chunk += struct.pack("<I", i)
        
    # Playlist Header Total Length = 20 + 1 * 4
    ptr_offset = playlist_header_offset + 20 + 4
    play_header_bytes += struct.pack("<I", ptr_offset)
    full_playlist_segment = play_header_bytes + playlist_chunk
    
    final_db = tunessd.construct() + full_track_segment + full_playlist_segment
    
    try:
        with open(os.path.join(itunes_dir, "iTunesSD"), "wb") as f:
            f.write(final_db)
    except Exception as e:
        print(f"\n [ERROR] Failed to write iTunesSD: {e}")
        sys.exit(1)
        
    print("\n")
    print("=" * 60)
    print(" SUMMARY REPORT ".center(60))
    print("=" * 60)
    print(f" 4G Native Database successfully generated.")
    print(f" Total playable tracks indexed: {num_tracks}")
    if skipped:
        print(f" Skipped unsupported files: {len(skipped)}")
        for skip in skipped[:5]:
            print(f"   - {os.path.basename(skip)}")
        if len(skipped) > 5:
            print(f"   ... and {len(skipped)-5} more.")
            
    print("\n [ READY TO EJECT ] ".center(60))
    print(" You may now safely disconnect your iPod.".center(60))
    print("=" * 60)
    
    time.sleep(3)

if __name__ == "__main__":
    main()
