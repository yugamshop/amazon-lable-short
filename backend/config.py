import os
from dataclasses import dataclass
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

@dataclass(frozen=True)
class Paths:
    data: Path
    static: Path = PROJECT_ROOT / 'static'
    output_directory: Path | None = None

    @property
    def database(self): return self.data / 'inventory.sqlite3'
    @property
    def images(self): return self.data / 'images'
    @property
    def output(self): return self.output_directory or PROJECT_ROOT / 'Output'

    def prepare(self):
        self.data.mkdir(parents=True, exist_ok=True)
        self.images.mkdir(parents=True, exist_ok=True)
        self.output.mkdir(parents=True, exist_ok=True)

def default_paths():
    return Paths(Path(os.environ.get('LABELDESK_DATA_DIR', str(PROJECT_ROOT / 'data'))).resolve())
