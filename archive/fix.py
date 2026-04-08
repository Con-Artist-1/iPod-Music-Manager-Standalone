import os

path = r"d:\Ipod\antigravity.py"
with open(path, "r", encoding="utf-8") as f:
    text = f.read()

old_fs = '''def format_size(bytes_val):
    """Human-readable file size."""
    if bytes_val < 1024:
        return f"{bytes_val} B"
    elif bytes_val < 1024 * 1024:
        return f"{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{bytes_val / (1024*1024):.1f} MB"
    else:
        return f"{bytes_val / (1024*1024*1024):.2f} GB"'''

new_fs = '''def format_size(bytes_val):
    """Human-readable file size."""
    sign = "-" if bytes_val < 0 else ""
    bytes_val = abs(bytes_val)
    if bytes_val < 1024:
        return f"{sign}{int(bytes_val)} B"
    elif bytes_val < 1024 * 1024:
        return f"{sign}{bytes_val / 1024:.1f} KB"
    elif bytes_val < 1024 * 1024 * 1024:
        return f"{sign}{bytes_val / (1024*1024):.1f} MB"
    else:
        return f"{sign}{bytes_val / (1024*1024*1024):.2f} GB"'''

text = text.replace(old_fs, new_fs)

old_sync1 = '''    return existing


def sync_to_ipod(ipod_path, source_files, target_format, target_bitrate, convert_all,'''
old_sync2 = '''    return existing

def sync_to_ipod(ipod_path, source_files, target_format, target_bitrate, convert_all,'''

new_sync = '''    return existing


def get_ipod_safe_key(folder, basename):
    """Generate the matching key used for existing files based on non-ASCII rules."""
    def is_ascii(s):
        return all(ord(c) < 128 for c in s)
    f_safe = folder if folder else "_root"
    if not is_ascii(f_safe):
        import hashlib
        f_safe = "F_" + hashlib.md5(f_safe.encode('utf-8', 'ignore')).hexdigest()[:8]
    b_safe = basename
    if not is_ascii(b_safe):
        import hashlib
        b_safe = "T_" + hashlib.md5(b_safe.encode('utf-8', 'ignore')).hexdigest()[:8]
    return (f_safe.lower(), b_safe.lower())


def sync_to_ipod(ipod_path, source_files, target_format, target_bitrate, convert_all,'''

text = text.replace(old_sync1, new_sync)
text = text.replace(old_sync2, new_sync)

old_sel = '''                folder = finfo["folder"] if finfo["folder"] else "_root"
                basename = os.path.splitext(os.path.basename(path))[0]
                key = (folder.lower(), basename.lower())'''

new_sel = '''                folder = finfo["folder"]
                basename = os.path.splitext(os.path.basename(path))[0]
                key = get_ipod_safe_key(folder, basename)'''

text = text.replace(old_sel, new_sel)

old_rec = '''            folder = f["folder"] if f["folder"] else "_root"
            basename = os.path.splitext(os.path.basename(f["path"]))[0]
            key = (folder.lower(), basename.lower())'''

new_rec = '''            folder = f["folder"]
            basename = os.path.splitext(os.path.basename(f["path"]))[0]
            key = get_ipod_safe_key(folder, basename)'''

text = text.replace(old_rec, new_rec)

with open(path, "w", encoding="utf-8") as f:
    f.write(text)

print("Replacement complete.")
