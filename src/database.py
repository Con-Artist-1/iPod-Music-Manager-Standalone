"""
iPod Music Manager — iTunesSD Database Builder
Binary record classes for 4th Gen iPod Shuffle iTunesSD format (Little-Endian).
"""

import os
import struct
import collections
import hashlib


class Record:
    """Base class: compiles ordered field definitions into packed Little-Endian byte sequences."""
    def __init__(self):
        self._struct = collections.OrderedDict()
        self._fields = {}

    def __getitem__(self, item):
        if item not in self._struct:
            raise KeyError(item)
        return self._fields.get(item, self._struct[item][1])

    def __setitem__(self, item, value):
        self._fields[item] = value

    def construct(self):
        output = b""
        for key in self._struct:
            fmt, default = self._struct[key]
            val = self._fields.get(key, default)
            output += struct.pack("<" + fmt, val)
        return output


class TunesSD(Record):
    """bdhs: Base Database Header (64 bytes)."""
    def __init__(self, num_tracks, num_playlists):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id",                    ("4s", b"bdhs")),
            ("unknown1",                     ("I",  0x02000003)),
            ("total_length",                 ("I",  64)),
            ("total_number_of_tracks",       ("I",  num_tracks)),
            ("total_number_of_playlists",    ("I",  num_playlists)),
            ("unknown2",                     ("Q",  0)),
            ("max_volume",                   ("B",  0)),
            ("voiceover_enabled",            ("B",  0)),
            ("unknown3",                     ("H",  0)),
            ("total_tracks_without_podcasts", ("I", num_tracks)),
            ("track_header_offset",          ("I",  64)),
            ("playlist_header_offset",       ("I",  0)),
            ("unknown4",                     ("20s", b"\x00" * 20)),
        ])


class TrackHeader(Record):
    """hths: Track Header. Size = 20 + (num_tracks * 4) bytes."""
    def __init__(self, num_tracks):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id",        ("4s", b"hths")),
            ("total_length",     ("I",  20 + num_tracks * 4)),
            ("number_of_tracks", ("I",  num_tracks)),
            ("unknown1",         ("Q",  0)),
        ])


class Track(Record):
    """rths: Individual track record (0x174 = 372 bytes)."""
    def __init__(self):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id",       ("4s", b"rths")),
            ("header_length",   ("I",  0x174)),
            ("start_at_pos_ms", ("I",  0)),
            ("stop_at_pos_ms",  ("I",  0)),
            ("volume_gain",     ("I",  0)),
            ("filetype",        ("I",  1)),
            ("filename",        ("256s", b"\x00" * 256)),
            ("bookmark",        ("I",  0)),
            ("dontskip",        ("B",  1)),
            ("remember",        ("B",  0)),
            ("unintalbum",      ("B",  0)),
            ("unknown",         ("B",  0)),
            ("pregap",          ("I",  0x200)),
            ("postgap",         ("I",  0x200)),
            ("numsamples",      ("I",  0)),
            ("unknown2",        ("I",  0)),
            ("gapless",         ("I",  0)),
            ("unknown3",        ("I",  0)),
            ("albumid",         ("I",  0)),
            ("track",           ("H",  1)),
            ("disc",            ("H",  0)),
            ("unknown4",        ("Q",  0)),
            ("dbid",            ("8s", b"\x00" * 8)),
            ("artistid",        ("I",  0)),
            ("unknown5",        ("32s", b"\x00" * 32)),
        ])

    def populate(self, ipod_path, display_name=None):
        """Set track metadata from an iPod-relative path like /iPod_Control/Music/F00/song.mp3"""
        self["filename"] = ipod_path.encode('utf-8')
        ext = os.path.splitext(ipod_path)[1].lower()
        if ext in (".m4a", ".m4b", ".m4p", ".aa"):
            self["filetype"] = 2
            if ext in (".m4b", ".aa"):
                self["dontskip"] = 0
                self["bookmark"] = 1
                self["remember"] = 1
        
        if display_name is None:
            display_name = os.path.splitext(os.path.basename(ipod_path))[0]
        self["dbid"] = hashlib.md5(display_name.encode('utf-8', 'ignore')).digest()[:8]


class PlaylistHeader(Record):
    """hphs: Playlist Header. Size = 20 + (num_playlists * 4) bytes."""
    def __init__(self, num_playlists):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id",                     ("4s", b"hphs")),
            ("total_length",                  ("I",  0x14 + num_playlists * 4)),
            ("number_of_playlists",           ("I",  num_playlists)),
            ("number_of_non_podcast_lists",   ("2s", b"\xFF\xFF")),
            ("number_of_master_lists",        ("2s", b"\x01\x00")),
            ("number_of_non_audiobook_lists", ("2s", b"\xFF\xFF")),
            ("unknown2",                      ("2s", b"\x00" * 2)),
        ])


class Playlist(Record):
    """lphs: Playlist record. 44 bytes + (num_tracks * 4) index bytes."""
    def __init__(self, num_tracks, listtype=1, dbid=None):
        super().__init__()
        self._struct = collections.OrderedDict([
            ("header_id",        ("4s", b"lphs")),
            ("total_length",     ("I",  44 + 4 * num_tracks)),
            ("number_of_songs",  ("I",  num_tracks)),
            ("number_of_nonaudio", ("I", num_tracks)),
            ("dbid",             ("8s", dbid if dbid else b"\x00" * 8)),
            ("listtype",         ("I",  listtype)),
            ("unknown1",         ("16s", b"\x00" * 16)),
        ])


# ══════════════════════════════════════════════════════════════════════════════
#  DATABASE BUILDER (shared between sync and rebuild)
# ══════════════════════════════════════════════════════════════════════════════

def build_itunes_db(all_tracks, playlists, voiceover_enabled=False,
                    original_titles=None, progress_cb=None, log_cb=None):
    """
    Build the iTunesSD binary database from a list of tracks and playlists.
    
    Args:
        all_tracks: list of iPod-relative paths (e.g. "/iPod_Control/Music/folder/song.mp3")
        playlists: dict of {folder_name: [ipod_paths]}
        voiceover_enabled: whether to set the VoiceOver flag
        original_titles: optional dict of {ipod_path: original_basename} for display names
        progress_cb: optional callback(current, total, phase)
        log_cb: optional callback(message)
    
    Returns:
        (final_db_bytes, num_tracks, num_playlists, track_dbids, playlist_dbids)
    """
    if original_titles is None:
        original_titles = {}

    def log(msg):
        if log_cb:
            log_cb(msg)

    def progress(cur, tot, phase=""):
        if progress_cb:
            progress_cb(cur, tot, phase)

    num_tracks = len(all_tracks)
    num_playlists = 1 + len(playlists)

    tunessd = TunesSD(num_tracks, num_playlists)
    if voiceover_enabled:
        tunessd["voiceover_enabled"] = 1
    track_header = TrackHeader(num_tracks)
    track_header_offset = 64

    track_header_bytes = track_header.construct()
    tracks_chunk = b""
    track_index_map = {}
    track_dbids = {}  # For VoiceOver: {ipod_path: (dbid_bytes, display_name)}

    for i, ipod_path_str in enumerate(all_tracks):
        track = Track()
        orig_title = original_titles.get(ipod_path_str)
        track.populate(ipod_path_str, orig_title)

        ptr_offset = track_header_offset + 20 + (num_tracks * 4) + len(tracks_chunk)
        track_header_bytes += struct.pack("<I", ptr_offset)
        tracks_chunk += track.construct()

        track_index_map[ipod_path_str] = i
        display_name = orig_title if orig_title else os.path.splitext(os.path.basename(ipod_path_str))[0]
        track_dbids[ipod_path_str] = (track["dbid"], display_name)
        progress(i + 1, num_tracks, "Indexing")

    full_track_segment = track_header_bytes + tracks_chunk

    playlist_header_offset = track_header_offset + len(full_track_segment)
    tunessd["playlist_header_offset"] = playlist_header_offset

    play_header = PlaylistHeader(num_playlists)
    play_header_base = play_header.construct()
    playlist_chunks = []
    playlist_dbids = {}  # For VoiceOver: {folder_name: dbid_bytes}

    # Master playlist
    master = Playlist(num_tracks, listtype=1)
    master_data = master.construct()
    for i in range(num_tracks):
        master_data += struct.pack("<I", i)
    playlist_chunks.append(master_data)

    # Folder playlists
    for folder_name in sorted(playlists.keys()):
        folder_tracks = playlists[folder_name]
        indices = [track_index_map[t] for t in folder_tracks if t in track_index_map]
        if not indices:
            continue
        dbid = hashlib.md5(folder_name.encode('utf-8')).digest()[:8]
        pl = Playlist(len(indices), listtype=2, dbid=dbid)
        pl_data = pl.construct()
        for idx in indices:
            pl_data += struct.pack("<I", idx)
        playlist_chunks.append(pl_data)
        playlist_dbids[folder_name] = dbid
        log(f"  Playlist '{folder_name}': {len(indices)} tracks")

    # Adjust if any playlists were skipped
    actual_playlists = len(playlist_chunks)
    if actual_playlists != num_playlists:
        tunessd = TunesSD(num_tracks, actual_playlists)
        if voiceover_enabled:
            tunessd["voiceover_enabled"] = 1
        tunessd["playlist_header_offset"] = playlist_header_offset
        play_header = PlaylistHeader(actual_playlists)
        play_header_base = play_header.construct()
        num_playlists = actual_playlists

    play_header_total_length = 20 + (num_playlists * 4)
    current_offset = playlist_header_offset + play_header_total_length
    play_header_with_ptrs = play_header_base
    for chunk in playlist_chunks:
        play_header_with_ptrs += struct.pack("<I", current_offset)
        current_offset += len(chunk)

    full_playlist_segment = play_header_with_ptrs + b"".join(playlist_chunks)
    final_db = tunessd.construct() + full_track_segment + full_playlist_segment

    return final_db, num_tracks, num_playlists, track_dbids, playlist_dbids

