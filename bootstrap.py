"""Standard Python setup; no administrator rights or system policy changes."""
from pathlib import Path
import os,sys,subprocess,venv,struct
root=Path(__file__).resolve().parent
os.chdir(root)
if sys.version_info<(3,11) or struct.calcsize('P')!=8:
    raise SystemExit('Install Python 3.12, 64-bit, then run start.bat again.')
python=root/'.venv'/'Scripts'/'python.exe'
if not python.exists():
    print('Creating the local app environment...');venv.create(root/'.venv',with_pip=True)
ready=root/'.venv'/'labeldesk-ready-v2'
if not ready.exists():
    args=[str(python),'-m','pip','install','-r',str(root/'requirements.txt')]
    if sys.version_info[:2]==(3,12) and (root/'wheelhouse').exists():
        args+=['--no-index','--find-links',str(root/'wheelhouse')];print('Installing bundled dependencies offline...')
    else:print('This Python version needs internet for its first dependency setup. Python 3.12 uses the offline bundle.')
    subprocess.run(args,check=True);ready.write_text('ready')
raise SystemExit(subprocess.call([str(python),str(root/'run.py')]))
