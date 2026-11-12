"""Run only on this computer. Close the console or press Ctrl+C to stop."""
import os
import socket
import threading
import time
import urllib.request
import webbrowser
import uvicorn

def open_when_ready():
    for _ in range(80):
        try:
            with urllib.request.urlopen('http://127.0.0.1:8765/api/health',timeout=1) as response:
                if response.status==200:
                    webbrowser.open('http://localhost:8765/backup?v=backup-4');return
        except OSError:time.sleep(.25)

if __name__=='__main__':
    try:
        with socket.socket() as probe:
            probe.bind(('127.0.0.1',8765))
    except OSError:
        print('Another LabelDesk copy is already running. Close its console window, then start this updated copy again.')
        raise SystemExit(1)
    if not os.environ.get('LABELDESK_NO_BROWSER'):threading.Thread(target=open_when_ready,daemon=True).start()
    print('\nLabelDesk: http://localhost:8765\nAll data stays on this PC. Press Ctrl+C to stop.\n')
    uvicorn.run('backend.main:app',host='127.0.0.1',port=8765,log_level='info')
