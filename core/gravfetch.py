# core/gravfetch.py
import os
import time
import pandas as pd
import logging
import urllib3
from requests import Session
from requests_pelican import get as rp_get  # Use alias to avoid conflict
from gwpy.timeseries import TimeSeries
from gwdatafind import find_urls

# Disable warnings globally (safe for public GWOSC)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gravfetch")

DEFAULT_GWFOUT = "./uploads/GWFout"
os.makedirs(DEFAULT_GWFOUT, exist_ok=True)

def log(msg: str, level: str = "info") -> str:
    """Log to console and return HTML-colored string for web terminal"""
    level = level.lower()
    level_map = {
        "debug": logging.DEBUG,
        "info": logging.INFO,
        "success": logging.INFO,
        "warning": logging.WARNING,
        "error": logging.ERROR,
        "critical": logging.CRITICAL,
    }
    log_level = level_map.get(level, logging.INFO)
    logger.log(log_level, msg)

    colors = {
        "info": "text-cyan-400",
        "success": "text-green-400",
        "warning": "text-yellow-400",
        "error": "text-red-500",
        "critical": "text-purple-500",
        "debug": "text-blue-400",
    }
    color_class = colors.get(level, "text-gray-400")
    prefix = f'<span class="{color_class} font-bold">[{level.upper()}]</span>'
    return f"{prefix} {msg}"

# === OSDF DOWNLOAD (with custom session to bypass SSL hostname mismatch) ===
def download_osdf(detector_code: str, frametype: str, segments: list[str], output_dir: str = DEFAULT_GWFOUT):
    os.makedirs(output_dir, exist_ok=True)
    channel = f"{detector_code}:{frametype}"
    ch_dir = os.path.join(output_dir, channel.replace(":", "_"))
    os.makedirs(ch_dir, exist_ok=True)
    fin_path = os.path.join(ch_dir, "fin.ffl")
    host = "https://datafind.gw-openscience.org"
    downloaded = 0

    # Create custom session with SSL verification disabled
    session = Session()
    session.verify = False

    for seg in segments:
        try:
            start, end = map(int, seg.split("_"))
        except Exception:
            yield log(f"Invalid segment format: {seg}", "error")
            continue

        segment_dir = os.path.join(ch_dir, f"{start}_{end}")
        os.makedirs(segment_dir, exist_ok=True)

        try:
            # Use custom session to bypass SSL hostname issue
            urls = find_urls(
                detector_code, frametype, start, end,
                urltype='osdf', host=host, session=session
            )
        except Exception as e:
            yield log(f"find_urls error {seg}: {e}", "error")
            continue

        if not urls:
            yield log(f"No files found for {seg}", "warning")
            continue

        yield log(f"Found {len(urls)} file(s) for {seg}", "info")

        for url in urls:
            filename = os.path.basename(url)
            filepath = os.path.join(segment_dir, filename)

            if os.path.exists(filepath):
                yield log(f"Already exists: {filename}", "info")
                continue

            try:
                yield log(f"Downloading {filename} ...", "info")
                # Use requests_pelican with verify=False as fallback
                r = rp_get(url, timeout=180, verify=False)
                r.raise_for_status()

                with open(filepath, "wb") as f:
                    f.write(r.content)

                # Parse timestamp and duration from filename
                parts = filename.split("-")
                timestamp = int(parts[-2])
                duration = int(parts[-1].replace(".gwf", ""))

                rel_path = os.path.relpath(filepath, os.getcwd()).replace("\\", "/")
                with open(fin_path, "a") as fin:
                    fin.write(f"./{rel_path} {timestamp} {duration} 0 0\n")

                downloaded += 1
                yield log(f"Saved {filename}", "success")
                time.sleep(1.0)  # Be gentle on server
            except Exception as e:
                yield log(f"Download failed {filename}: {e}", "error")

    yield log(f"OSDF complete – {downloaded} file(s) downloaded", "success")

# === NDS / Public Download (no SSL issue – uses gwpy.fetch) ===
def download_nds(channel: str, segments: list[str], output_dir: str = DEFAULT_GWFOUT):
    os.makedirs(output_dir, exist_ok=True)
    ch_dir = os.path.join(output_dir, channel.replace(":", "_"))
    os.makedirs(ch_dir, exist_ok=True)
    fin_path = os.path.join(ch_dir, "fin.ffl")

    for seg in segments:
        try:
            start, end = map(int, seg.split("_"))
        except Exception:
            yield log(f"Bad segment format: {seg}", "error")
            continue

        tdir = os.path.join(ch_dir, f"{start}_{end}")
        os.makedirs(tdir, exist_ok=True)
        outfile = os.path.join(tdir, f"{channel.replace(':', '_')}_{start}_{end}.gwf")

        if os.path.exists(outfile):
            yield log(f"Already fetched {seg}", "info")
            continue

        try:
            yield log(f"Fetching {channel} {start}-{end} via NDS...", "info")
            data = TimeSeries.fetch(channel, start, end, host="nds.gwosc.org")
            data.write(outfile)

            rel_path = os.path.relpath(outfile, os.getcwd()).replace("\\", "/")
            with open(fin_path, "a") as f:
                f.write(f"./{rel_path} {start} {end - start} 0 0\n")

            yield log(f"Saved {seg}", "success")
        except Exception as e:
            yield log(f"NDS fetch failed {seg}: {e}", "error")
