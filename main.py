from fastapi import FastAPI, Form
from fastapi.responses import HTMLResponse, FileResponse
import os
import subprocess
import uuid

app = FastAPI()

@app.get("/", response_class=HTMLResponse)
async def home():
    return """
    <html><body style="font-family:Arial; text-align:center; margin-top:100px;">
      <h1>GWeasy Cloud</h1>
      <form action="/fetch" method="post">
        Channel: <input name="channel" value="H1:GDS-CALIB_STRAIN" size="30"><br><br>
        GPS start: <input name="start" value="1328175344" size="10">
        GPS end:   <input name="end" value="1328175360" size="10"><br><br>
        <button type="submit" style="font-size:18px;padding:10px">Fetch Data → Download .gwf</button>
      </form>
    </body></html>
    """

@app.post("/fetch")
async def fetch(channel: str = Form(), start: str = Form(), end: str = Form()):
    job_id = str(uuid.uuid4())[:8]
    os.makedirs("results", exist_ok=True)
    outfile = f"results/{job_id}_{channel.replace(':','_')}_{start}_{end}.gwf"
    
    # Run your existing Gravfetch code (copy-pasted & slightly adapted)
    cmd = f"python -c \"
from gwpy.timeseries import TimeSeries;
data = TimeSeries.fetch_open_data(channel.split(':')[0], int(start), int(end));
data.write('{outfile}')
print('DONE')
\""
    subprocess.run(cmd, shell=True)
    
    if os.path.exists(outfile):
        return FileResponse(outfile, filename=os.path.basename(outfile))
    else:
        return HTMLResponse("<h2>Failed :(</h2>")
