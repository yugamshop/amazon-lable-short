# amazon lable short

LabelDesk is a local Windows web app for sorting shipping-label PDFs by SKU and managing picking inventory. It runs at **http://localhost:8765** and processes files on your PC.

## Start from source

1. Install Python 3.12 (64-bit), including the Python launcher or Add Python to PATH.
2. Download this repository with **Code → Download ZIP**, then extract it.
3. Double-click **start.bat** and keep the console open.
4. The first run downloads Python dependencies. Later runs work offline.

For scanned/image-only PDFs, install Tesseract OCR with English language data. The app checks `ocr/tesseract.exe`, the standard Windows installation folder, and PATH. OCR binaries and Python runtimes are not committed in this source repository.

## Process labels

1. Open Upload and Sort, select one or more PDFs, and process them.
2. SKU is the default sorting method. Amazon label-only pages can use a printed `SKU [quantity]` footer, including multiple items on one line.
3. Review uncertain OCR readings and correct SKU/quantity fields before printing. A preview page shows 25 orders; exports include the whole batch.
4. Generate and download the sorted PDF, pick-list PDF/Excel, and Needs Check report. Labels stay with matched invoice pages.

Image-only labels use local OCR. Recognition is not guaranteed for every marketplace/layout. Unreadable orders go to the end; generation stops when no readable orders exist. Multi-product labels remain whole and sort by their first product.

## Inventory and backups

Inventory supports SKU/ASIN/FSN, aliases, rack locations, stock, notes, and up to three product photos. Import/export inventory using Excel or CSV. Pick lists can be refreshed after inventory changes; stock is not automatically deducted.

Open **Inventory Backup & Restore**, or visit **http://localhost:8765/backup**. Download a full inventory ZIP including photos. Restoring replaces inventory and first saves a recovery backup. Shipping PDFs and generated outputs are separate from inventory backups.

Local data is stored in `data/`, and generated files in `Output/`. These directories are excluded from Git. Never commit customer labels, inventory backups, personal photos, or credentials.

## Development

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe run.py
```

Run tests with:

```powershell
.\.venv\Scripts\python.exe -m pip install pytest httpx
.\.venv\Scripts\python.exe -m pytest tests -q
```

Backend modules are in `backend/`; HTML, CSS and JavaScript in `static/`. The server binds to localhost only. History, configurable marking placement, and independent splitting of several labels on one page remain unimplemented. Original printed content is preserved when reordering; optional quantity highlights and page numbers are overlays.

Third-party packages retain their respective licenses. This repository does not bundle their executables or training data.
