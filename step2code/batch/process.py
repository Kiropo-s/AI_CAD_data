"""Hard deadlines must terminate the Windows venv launcher AND its native worker."""
import os
import signal
import subprocess


def terminate(process):
    if process.poll() is not None:return
    if os.name=='nt':
        completed=subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],
            stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,timeout=15)
        if completed.returncode and process.poll() is None:
            raise RuntimeError('Could not terminate isolated worker process tree')
    else:
        try:os.killpg(process.pid,signal.SIGKILL)
        except ProcessLookupError:pass
    process.wait(timeout=15)


def isolation_options():
    return {} if os.name=='nt' else dict(start_new_session=True)
