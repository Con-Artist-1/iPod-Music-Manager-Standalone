import os, subprocess, time, concurrent.futures

def get_duration(path):
    try:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
        cmd = ["ffmpeg", "-i", path]
        result = subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", creationflags=creationflags)
        for line in result.stderr.splitlines():
            if "Duration:" in line:
                # "  Duration: 00:15:34.25, start: 0.000000, bitrate: 128 kbps"
                time_str = line.split("Duration:")[1].split(",")[0].strip()
                h, m, s = time_str.split(":")
                return float(h) * 3600 + float(m) * 60 + float(s)
    except Exception:
        pass
    return None

if __name__ == "__main__":
    t0 = time.time()
    # just create dummy test
    print(time.time() - t0)
