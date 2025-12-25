# app.py
from fastapi import FastAPI, Request, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os, uuid, shutil
from core.gravfetch import download_osdf, download_nds
from core.omicron import run_omicron, generate_fin_ffl
import re

app = FastAPI(title="GWeasy Web")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

UPLOADS = "./uploads"
os.makedirs(UPLOADS, exist_ok=True)

# === Home ===
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# === Gravfetch Page ===
@app.get("/gravfetch", response_class=HTMLResponse)
async def gravfetch_page(request: Request):
    return templates.TemplateResponse("gravfetch.html", {"request": request})

# === Omicron Page ===
@app.get("/omicron", response_class=HTMLResponse)
async def omicron_page(request: Request):
    return templates.TemplateResponse("omicron.html", {"request": request})

# === Live log streaming (used by both tabs) ===
def event_stream(generator):
    yield "data: GWeasy Web terminal ready...\n\n"
    for line in generator:
        yield f"data: {line}\n\n"

@app.post("/api/gravfetch/osdf")
async def api_osdf(
    detector_code: str = Form(...),
    frametype: str = Form(...),
    segments: str = Form(...)  # e.g. "1234567890_1234577890,..."
):
    segs = segments.split(",")
    return StreamingResponse(event_stream(download_osdf(detector_code, frametype, segs)), media_type="text/event-stream")

@app.post("/api/gravfetch/nds")
async def api_nds(channel: str = Form(...), segments: str = Form(...)):
    segs = [s.strip() for s in segments.split(",") if s.strip()]
    return StreamingResponse(event_stream(download_nds(channel, segs)), media_type="text/event-stream")

@app.post("/api/omicron/run")
async def api_omicron(channel_dir: str = Form(...), segments: str = Form(...)):
    segs = [s.strip() for s in segments.split(",") if s.strip()]
    ffl = generate_fin_ffl(channel_dir, segs)
    return StreamingResponse(event_stream(run_omicron(ffl)), media_type="text/event-stream")

# Download results
@app.get("/download/{path:path}")
async def download(path: str):
    file = os.path.join(UPLOADS, path)
    if os.path.exists(file):
        return FileResponse(file)
    return {"error": "File not found"}
import glob

# Serve config.txt
@app.get("/config.txt")
async def get_config():
    return FileResponse("config.txt")

@app.post("/api/config")
async def save_config(content: str = Form(...)):
    with open("config.txt", "w") as f:
        f.write(content)
    return {"status": "saved"}

# List channels in GWFout
@app.get("/api/channels")
async def list_channels():
    channels = []
    for d in glob.glob("./uploads/GWFout/*"):
        if os.path.isdir(d):
            name = os.path.basename(d).replace("_", ":", 1)
            channels.append({"name": name, "path": d})
    return channels

# List segments inside a channel directory
@app.get("/api/segments")
async def list_segments(dir: str):
    segments = []
    for d in glob.glob(f"{dir}/*"):
        if os.path.isdir(d) and "_" in os.path.basename(d):
            segments.append(os.path.basename(d))
    return sorted(segments)

import subprocess
from gwdatafind import find_types
from gwpy.detector import ChannelList

# === OSDF: Get Frame Types ===
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
        return ["Error fetching types"]

# === OSDF: Get Time Segments ===
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
        return ["Error fetching segments"]

# === NDS: Get Groups (for selected detector) ===
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

# === NDS: Get Channels in Group ===
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

from fastapi import BackgroundTasks

current_job_log = []

@app.post("/api/gravfetch/osdf")
async def trigger_osdf_download(data: dict, background_tasks: BackgroundTasks):
    global current_job_log
    current_job_log = []
    segments = data["segments"]
    background_tasks.add_task(run_osdf_background, data["detector"], data["frametype"], segments)
    return {"status": "started"}

def run_osdf_background(detector, frametype, segments):
    global current_job_log
    for line in download_osdf(detector, frametype, segments):
        current_job_log.append(line)

@app.get("/api/gravfetch/osdf/stream")
async def osdf_stream():
    def event_generator():
        global current_job_log
        yielded = 0
        while True:
            if yielded < len(current_job_log):
                yield current_job_log[yielded]
                yielded += 1
            elif len(current_job_log) > yielded:
                yield current_job_log[yielded]
                yielded += 1
            await asyncio.sleep(0.5)
    return StreamingResponse(event_generator(), media_type="text/event-stream")
