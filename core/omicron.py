# core/omicron.py
import os
import subprocess
import platform
from pathlib import Path

OMICRON_OUT = "./uploads/OmicronOut"
os.makedirs(OMICRON_OUT, exist_ok=True)

def generate_fin_ffl(channel_dir, selected_segments):
    """Same logic as your generate_fin_ffl()"""
    fin_path = Path(channel_dir) / "fin.ffl"
    with open(fin_path, "w") as f:
        for seg in selected_segments:
            seg_path = Path(channel_dir) / seg
            gwfs = list(seg_path.glob("*.gwf"))
            if not gwfs:
                continue
            gwf = gwfs[0]
            rel = gwf.relative_to(Path.cwd()).as_posix()
            start = int(seg.split("_")[0])
            duration = int(seg.split("_")[1]) - start
            f.write(f"./{rel} {start} {duration} 0 0\n")
    return str(fin_path)

def run_omicron(ffl_path, config_path="config.txt", output_dir=OMICRON_OUT):
    if not os.path.exists(ffl_path):
        yield "[ERROR] .ffl file not found"
        return

    with open(ffl_path) as f:
        lines = [l.strip().split() for l in f if l.strip()]
    if not lines:
        yield "[ERROR] Empty .ffl"
        return
    first_time = lines[0][1]
    last_time = lines[-1][1]

    if platform.system() == "Windows":
        # Exact same WSL logic you wrote
        cmd = [
            "wsl", "bash", "-lic",
            f"cd '{os.getcwd()}' && "
            f"omicron {first_time} {last_time} {config_path} > omicron.out 2>&1"
        ]
    else:
        cmd = f"omicron {first_time} {last_time} {config_path} > omicron.out 2>&1"

    yield "[INFO] Starting OMICRON..."
    process = subprocess.Popen(cmd, shell=isinstance(cmd, str), stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    while True:
        line = process.stdout.readline()
        if line == "" and process.poll() is not None:
            break
        if line:
            yield line.strip()

    if process.returncode == 0:
        yield "[SUCCESS] OMICRON finished – results in ./uploads/OmicronOut"
    else:
        yield f"[ERROR] OMICRON failed (code {process.returncode})"