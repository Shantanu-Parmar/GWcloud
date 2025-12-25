# core/gravfetch.py
import os
import time
import threading
import pandas as pd
import subprocess
import requests_pelican as rp
from gwpy.timeseries import TimeSeries, TimeSeriesDict
from gwosc.locate import get_urls
from gwdatafind import find_urls
import logging
import urllib3

# Disable SSL warnings (safe for public GWOSC data)
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("gravfetch")

DEFAULT_GWFOUT = "./uploads/GWFout"
os.makedirs(DEFAULT_GWFOUT, exist_ok=True)

# Safe logging function
def log(msg: str, level: str = "info") -> str:
    """Safe logging + returns colored string for SSE"""
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
    # Pretty prefix for web terminal
    colors = {
        "info": "cyan",
        "success": "text-green-400",
        "warning": "text-yellow-400",
        "error": "text-red-500",
        "critical": "text-purple-500",
        "debug": "text-blue-400",
    }
    color_class = colors.get(level, "text-gray-400")
    prefix = f'<span class="{color_class} font-bold">[{level.upper()}]</span>'
    return f"{prefix} {msg}"

# === OSDF DOWNLOAD ===
def download_osdf(detector_code, frametype, segments, output_dir=DEFAULT_GWFOUT):
    os.makedirs(output_dir, exist_ok=True)
    channel = f"{detector_code}:{frametype}"
    ch_dir = os.path.join(output_dir, channel.replace(":", "_"))
    os.makedirs(ch_dir, exist_ok=True)
    fin_path = os.path.join(ch_dir, "fin.ffl")
    host = "https://datafind.gw-openscience.org"
    downloaded = 0
    for seg in segments:
        try:
            start, end = map(int, seg.split("_"))
        except:
            yield log(f"Invalid segment: {seg}", "error")
            continue
        segment_dir = os.path.join(ch_dir, f"{start}_{end}")
        os.makedirs(segment_dir, exist_ok=True)
        try:
            urls = find_urls(detector_code, frametype, start, end, urltype='osdf', host=host)
        except Exception as e:
            yield log(f"find_urls error {seg}: {e}", "error")
            continue
        if not urls:
            yield log(f"No files for {seg}", "warning")
            continue
        for url in urls:
            filename = url.split("/")[-1]
            filepath = os.path.join(segment_dir, filename)
            if os.path.exists(filepath):
                yield log(f"Already exists: {filename}", "info")
                continue
            try:
                yield log(f"Downloading {filename} ...", "info")
                # Critical fix: disable SSL verification for OSDF on Render
                r = rp.get(url, timeout=180, verify=False)
                r.raise_for_status()
                with open(filepath, "wb") as f:
                    f.write(r.content)
                # write to fin.ffl
                rel = os.path.relpath(filepath, os.getcwd()).replace("\\", "/")
                duration = int(filename.split("-")[-1].replace(".gwf", ""))
                timestamp = int(filename.split("-")[-2])
                with open(fin_path, "a") as fin:
                    fin.write(f"./{rel} {timestamp} {duration} 0 0\n")
                downloaded += 1
                yield log(f"Saved {filename}", "success")
                time.sleep(1.5)
            except Exception as e:
                yield log(f"Failed {filename}: {e}", "error")
    yield log(f"OSDF complete – {downloaded} files", "success")

# === NDS / Public Download ===
def download_nds(channel, segments, output_dir=DEFAULT_GWFOUT):
    os.makedirs(output_dir, exist_ok=True)
    ch_dir = os.path.join(output_dir, channel.replace(":", "_"))
    os.makedirs(ch_dir, exist_ok=True)
    fin_path = os.path.join(ch_dir, "fin.ffl")
    for seg in segments:
        try:
            start, end = map(int, seg.split("_"))
        except:
            yield log(f"Bad segment {seg}", "error")
            continue
        tdir = os.path.join(ch_dir, f"{start}_{end}")
        os.makedirs(tdir, exist_ok=True)
        outfile = os.path.join(tdir, f"{channel.replace(':','_')}_{start}_{end}.gwf")
        if os.path.exists(outfile):
            yield log(f"Already fetched {seg}", "info")
            continue
        try:
            yield log(f"Fetching {channel} {start}-{end} ...", "info")
            data = TimeSeries.fetch(channel, start, end)
            data.write(outfile)
            rel = os.path.relpath(outfile, os.getcwd()).replace("\\", "/")
            with open(fin_path, "a") as f:
                f.write(f"./{rel} {start} {end-start} 0 0\n")
            yield log(f"Saved {seg}", "success")
        except Exception as e:
            yield log(f"Failed {seg}: {e}", "error")
