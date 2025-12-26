# app.py
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import os
import glob
import asyncio
import re
import subprocess
from gwdatafind import find_types
from gwpy.detector import ChannelList
import requests
from requests import Session
from core.gravfetch import download_osdf, download_nds
from core.omicron import run_omicron, generate_fin_ffl
import zipfile
from fastapi.responses import FileResponse

app = FastAPI(title="GWcloud - GWeasy Web")

# # Mount static folder
# import os
# from fastapi.staticfiles import StaticFiles
# # Create static dir if not exists (safe on Azure/Render)
# os.makedirs("static", exist_ok=True)
# app.mount("/static", StaticFiles(directory="static"), name="static")


# Templates
templates = Jinja2Templates(directory="templates")

# Uploads directory
UPLOADS = "./uploads"
os.makedirs(UPLOADS, exist_ok=True)

# Global log for live streaming
current_job_log: list[str] = []

# === Pages ===
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

@app.get("/gravfetch", response_class=HTMLResponse)
async def gravfetch_page(request: Request):
    return templates.TemplateResponse("gravfetch.html", {"request": request})

@app.get("/omicron", response_class=HTMLResponse)
async def omicron_page(request: Request):
    return templates.TemplateResponse("omicron.html", {"request": request})

# === NDS Download (direct streaming - kept as is) ===
@app.post("/api/gravfetch/nds")
async def api_nds(channel: str, segments: str):
    segs = [s.strip() for s in segments.split(",") if s.strip()]
    def generator():
        for line in download_nds(channel, segs):
            yield line
    return StreamingResponse(generator(), media_type="text/plain")

# === Omicron Run (direct streaming - kept as is) ===
@app.post("/api/omicron/run")
async def api_omicron(channel_dir: str, segments: str):
    segs = [s.strip() for s in segments.split(",") if s.strip()]
    ffl = generate_fin_ffl(channel_dir, segs)
    def generator():
        for line in run_omicron(ffl):
            yield line
    return StreamingResponse(generator(), media_type="text/plain")

# === File Download ===
@app.get("/download/{path:path}")
async def download(path: str):
    file = os.path.join(UPLOADS, path)
    if os.path.exists(file):
        return FileResponse(file)
    return {"error": "File not found"}

# === Config.txt ===
@app.get("/config.txt")
async def get_config():
    return FileResponse("config.txt")

@app.post("/api/config")
async def save_config(content: str):
    with open("config.txt", "w") as f:
        f.write(content)
    return {"status": "saved"}

# === List channels and segments in GWFout ===
@app.get("/api/channels")
async def list_channels():
    channels = []
    for d in glob.glob("./uploads/GWFout/*"):
        if os.path.isdir(d):
            name = os.path.basename(d).replace("_", ":", 1)
            channels.append({"name": name, "path": d})
    return channels

@app.get("/api/segments")
async def list_segments(dir: str):
    segments = []
    for d in glob.glob(f"{dir}/*"):
        if os.path.isdir(d) and "_" in os.path.basename(d):
            segments.append(os.path.basename(d))
    return sorted(segments)

# === OSDF Dropdown APIs (using CLI - working on Render) ===
@app.get("/api/osdf/frametypes")
async def api_osdf_frametypes(detector: str):
    if detector not in ["H", "L", "V", "K"]:
        return []
    try:
        cmd = ["gw_data_find", "-r", "datafind.gwosc.org", "-o", detector, "--show-types"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        types = [line.strip() for line in result.stdout.splitlines() if line.strip() and not line.startswith("#")]
        return types or ["No frame types available"]
    except Exception as e:
        return [f"Error: {str(e)}"]

@app.get("/api/osdf/segments")
async def api_osdf_segments(detector: str, frametype: str):
    if not detector or not frametype:
        return []
    try:
        cmd = ["gw_data_find", "-r", "datafind.gwosc.org", "-o", detector, "-t", frametype, "--show-times"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        segments = []
        for line in result.stdout.splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                parts = line.split()
                if len(parts) >= 4:
                    start, end = int(parts[1]), int(parts[2])
                    segments.append(f"{start}_{end}")
        return segments or ["No segments available"]
    except Exception as e:
        return [f"Error: {str(e)}"]

# === NDS Dropdown APIs ===
@app.get("/api/nds/groups")
async def api_nds_groups(detector: str):
    if detector not in ["H1", "L1", "V1", "K1"]:
        return ["Invalid detector"]
    try:
        chanlist = ChannelList.query_nds2(f'{detector}:*', host='nds.gwosc.org')
        groups = set()
        for chan in chanlist:
            match = re.match(rf'^{detector}:([A-Z]+)-?.*', chan.name)
            if match:
                groups.add(match.group(1))
        return sorted(groups) or ["No groups available"]
    except Exception as e:
        return ["Error fetching groups"]

@app.get("/api/nds/channels")
async def api_nds_channels(detector: str, group: str):
    try:
        chanlist = ChannelList.query_nds2(f'{detector}:*', host='nds.gwosc.org')
        channels = []
        for chan in chanlist:
            if re.match(rf'^{detector}:{group}-?.*', chan.name):
                channels.append(f"{chan.name} ({chan.sample_rate})")
        return channels or ["No channels available"]
    except Exception as e:
        return ["Error fetching channels"]

# === NEW OSDF DOWNLOAD SYSTEM (Pydantic + Background + Live Streaming) ===

class OSDFRequest(BaseModel):
    detector: str
    frametype: str
    segments: list[str]

@app.post("/api/gravfetch/osdf")
async def trigger_osdf_download(request: OSDFRequest, background_tasks: BackgroundTasks):
    global current_job_log
    current_job_log = []  # Reset for new job

    current_job_log.append(f"[INFO] Starting OSDF download for {request.detector}:{request.frametype}")
    current_job_log.append(f"[INFO] Requested segments: {', '.join(request.segments)}")

    background_tasks.add_task(run_osdf_background, request.detector, request.frametype, request.segments)

    return {"status": "started", "message": "Download started – see live terminal"}

# def run_osdf_background(detector: str, frametype: str, segments: list[str]):
#     global current_job_log
#     try:
#         for log_line in download_osdf(detector, frametype, segments):
#             current_job_log.append(log_line)
#         current_job_log.append("[SUCCESS] OSDF download completed successfully!")
#     except Exception as e:
#         current_job_log.append(f"[ERROR] Download failed: {str(e)}")
        
def run_osdf_background(detector: str, frametype: str, segments: list[str]):
    global current_job_log
    try:
        for log_line in download_osdf(detector, frametype, segments):
            current_job_log.append(log_line)

        # After success, create ZIP
        channel = f"{detector}:{frametype}"
        ch_dir_name = channel.replace(":", "_")
        channel_path = os.path.join("./uploads/GWFout", ch_dir_name)
        zip_path = f"/tmp/{ch_dir_name}.zip"

        if os.path.exists(channel_path):
            import zipfile
            with zipfile.ZipFile(zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(channel_path):
                    for file in files:
                        full_path = os.path.join(root, file)
                        arcname = os.path.relpath(full_path, "./uploads/GWFout")
                        zipf.write(full_path, arcname)
            current_job_log.append(f"[SUCCESS] ZIP ready: /download_zip/{ch_dir_name}")
        else:
            current_job_log.append("[ERROR] No files to zip")

    except Exception as e:
        current_job_log.append(f"[ERROR] {str(e)}")
        

@app.get("/api/gravfetch/osdf/stream")
async def osdf_stream():
    async def event_generator():
        global current_job_log
        last_seen = 0
        while True:
            if last_seen < len(current_job_log):
                for i in range(last_seen, len(current_job_log)):
                    yield f"data: {current_job_log[i]}\n\n"
                last_seen = len(current_job_log)
            await asyncio.sleep(0.5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")

@app.get("/api/downloads")
async def api_downloads():
    base_dir = "./uploads/GWFout"
    channels = []

    if not os.path.exists(base_dir):
        return {"channels": channels}

    try:
        for ch_dir_name in sorted(os.listdir(base_dir)):
            ch_full_path = os.path.join(base_dir, ch_dir_name)
            if not os.path.isdir(ch_full_path):
                continue

            # Convert directory name back to channel name: replace only the first '_' with ':'
            channel_name = ch_dir_name.replace("_", ":", 1)

            segments = []
            for seg_dir_name in sorted(os.listdir(ch_full_path)):
                seg_full_path = os.path.join(ch_full_path, seg_dir_name)

                # Skip fin.ffl and non-directories
                if seg_dir_name == "fin.ffl":
                    continue
                if not os.path.isdir(seg_full_path):
                    continue

                gwf_files = []
                for filename in sorted(os.listdir(seg_full_path)):
                    if filename.endswith(".gwf"):
                        file_full_path = os.path.join(seg_full_path, filename)
                        rel_path = os.path.join("uploads/GWFout", ch_dir_name, seg_dir_name, filename).replace("\\", "/")
                        size = os.path.getsize(file_full_path)
                        gwf_files.append({
                            "name": filename,
                            "path": rel_path,
                            "size": size
                        })

                # Add fin.ffl for this channel (at channel level)
                fin_full_path = os.path.join(ch_full_path, "fin.ffl")
                fin_info = None
                if os.path.exists(fin_full_path):
                    fin_rel_path = os.path.join("uploads/GWFout", ch_dir_name, "fin.ffl").replace("\\", "/")
                    fin_info = {
                        "name": "fin.ffl",
                        "path": fin_rel_path,
                        "size": os.path.getsize(fin_full_path)
                    }

                segments.append({
                    "name": seg_dir_name,
                    "duration": int(seg_dir_name.split("_")[1]) - int(seg_dir_name.split("_")[0]),
                    "files": gwf_files,
                    "fin": fin_info  # Will be same for all segments of channel
                })

            if segments:  # Only add channel if it has segments
                channels.append({
                    "name": channel_name,
                    "dir": ch_dir_name,
                    "segments": segments
                })

    except Exception as e:
        print(f"Error scanning downloads: {e}")
        return {"channels": channels, "error": str(e)}

    return {"channels": channels}

@app.get("/downloads", response_class=HTMLResponse)
async def downloads_page(request: Request):
    return templates.TemplateResponse("downloads.html", {"request": request})
    
@app.get("/debug/files")
async def debug_files():
    files = []
    base = "./uploads/GWFout"
    if os.path.exists(base):
        for root, _, fs in os.walk(base):
            for f in fs:
                rel = os.path.relpath(os.path.join(root, f))
                files.append(rel)
    return {"files": files or "No files (cleared or not downloaded yet)"}


@app.get("/download_zip/{ch_dir_name}")
async def download_zip(ch_dir_name: str):
    zip_path = f"/tmp/{ch_dir_name}.zip"
    if os.path.exists(zip_path):
        return FileResponse(zip_path, media_type="application/zip", filename=f"{ch_dir_name}_data.zip")
    return {"error": "ZIP not ready"}
