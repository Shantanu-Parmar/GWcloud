# web.py — GWeasy Cloud: Full Gravfetch + Omicron in the browser
import os
import uuid
import threading
from fastapi import FastAPI, Form, File, UploadFile, HTTPException
from fastapi.responses import HTMLResponse, FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.requests import Request
import logging

# Import your original code (no GUI will launch!)
from gweasy import GravfetchApp, OmicronApp

# === Setup ===
app = FastAPI(title="GWeasy Cloud", description="Zero-install GW data + Omicron")
app.mount("/static", StaticFiles(directory="static"), name="static")
templates = Jinja2Templates(directory="templates")

# Fake parent + logger so your classes don't try to open windows
class FakeParent:
    def append_output(self, msg, level="info"): 
        print(f"[{level.upper()}] {msg}")
        logging.info(msg)

fake_parent = FakeParent()
log_callback = lambda msg, lvl="info": print(f"[{lvl.upper()}] {msg}")

# Initialize your apps (they will NOT open any window)
grav = GravfetchApp(fake_parent, log_callback)
omicron = OmicronApp(fake_parent, log_callback)

RESULTS = "results"
os.makedirs(RESULTS, exist_ok=True)

# === Web Pages ===
@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})

# === Public Data Fetch (Gravfetch) ===
@app.post("/fetch")
async def fetch_public(
    request: Request,
    channel: str = Form(...),
    start: int start: int = Form(...),
    end: int = Form(...)
):
    if end - start > 3600:
        raise HTTPException(400, "Max 1 hour for demo")

    job_id = str(uuid.uuid4())[:8]
    out_dir = f"{RESULTS}/{job_id}"
    os.makedirs(out_dir, exist_ok=True)
    outfile = f"{out_dir}/{channel.replace(':', '_')}_{start}_{end}.gwf"

    def run():
        try:
            from gwpy.timeseries import TimeSeries
            data = TimeSeries.fetch_open_data(channel.split(":")[0], start, end)
            data.write(outfile)
            print(f"SUCCESS: Saved {outfile}")
        except Exception as e:
            print(f"ERROR: {e}")
            if os.path.exists(outfile):
                os.remove(outfile)

    threading.Thread(target=run, daemon=True).start()

    # Wait max 90 seconds
    for _ in range(90):
        if os.path.exists(outfile):
            return FileResponse(outfile, filename=os.path.basename(outfile))
        await asyncio.sleep(1)

    raise HTTPException(504, "Timeout — try shorter segment")

# === Omicron (upload .gwf or use fin.ffl) ===
@app.post("/omicron")
async def run_omicron(file: UploadFile = File(...)):
    if not file.filename.endswith((".gwf", ".ffl")):
        raise HTTPException(400, "Only .gwf or .ffl")

    job_id = str(uuid.uuid4())[:8]
    job_dir = f"{RESULTS}/{job_id}"
    os.makedirs(job_dir, exist_ok=True)
    path = f"{job_dir}/{file.filename}"
    contents = await file.read()
    with open(path, "wb") as f:
        f.write(contents)

    def run():
        # Use your existing OmicronApp logic
        omicron.project_dir = job_dir
        omicron.config_path = os.path.join(job_dir, "config.txt")
        # Create minimal config if missing
        if not os.path.exists(omicron.config_path):
            with open(omicron.config_path, "w") as f:
                f.write(omicron.DEFAULT_CONFIG)
        omicron.run_omicron()  # This uses your full Omicron pipeline

    threading.Thread(target=run, daemon=True).start()
    return {"status": "Omicron started", "job_id": job_id, "check": f"/results/{job_id}"}

@app.get("/results/{job_id:path}")
async def list_results(job_id: str):
    path = f"{RESULTS}/{job_id}"
    if not os.path.exists(path):
        raise HTTPException(404)
    files = os.listdir(path)
    html = "<h2>Omicron Results</h2><ul>"
    for f in files:
        html += f'<li><a href="/download/{job_id}/{f}">{f}</a></li>'
    html += "</ul>"
    return HTMLResponse(html)

@app.get("/download/{job_id:path}/{filename:path}")
async def download(job_id: str, filename: str):
    path = f"{RESULTS}/{job_id}/{filename}"
    if not os.path.exists(path):
        raise HTTPException(404)
    return FileResponse(path, filename=filename)

# === Run with: uvicorn web:app --host 0.0.0.0 --port $PORT ===
