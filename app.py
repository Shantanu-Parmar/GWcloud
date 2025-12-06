# app.py
from fastapi import FastAPI, Request, Form, File, UploadFile, BackgroundTasks
from fastapi.responses import HTMLResponse, StreamingResponse, FileResponse
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles
import os, uuid, shutil
from core.gravfetch import download_osdf, download_nds
from core.omicron import run_omicron, generate_fin_ffl

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