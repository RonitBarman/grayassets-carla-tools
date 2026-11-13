"""Explicit CARLA server lifecycle for step 1; never kills unrelated servers."""
import os
from pathlib import Path
import signal
import socket
import subprocess
import time


def start_carla_server(carla_root, port=2000):
    with socket.socket() as check:
        check.settimeout(1)
        if check.connect_ex(('localhost', port)) == 0:
            raise RuntimeError(f'Port {port} is already in use. Set START_SERVER=False to use the running CARLA server.')
    executable = Path(carla_root).resolve() / 'CarlaUE4.sh'
    process = subprocess.Popen([str(executable), '-RenderOffScreen', f'-carla-rpc-port={port}'],
        cwd=str(executable.parent), start_new_session=True)
    time.sleep(10)
    if process.poll() is not None:
        raise RuntimeError(f'CARLA exited with code {process.returncode}')
    return process


def stop_carla_server(process):
    if process.poll() is None:
        os.killpg(process.pid, signal.SIGTERM)
        try:
            process.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.wait()
