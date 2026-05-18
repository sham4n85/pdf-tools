# PDF Tools

A self-hosted web app with 24 PDF tools — merge, split, compress, convert, OCR, sign, redact and more. No files are stored on the server; everything is processed in memory and downloaded directly to your browser.

![Python](https://img.shields.io/badge/Python-3.11-blue) ![Docker](https://img.shields.io/badge/Docker-ready-blue) ![Port](https://img.shields.io/badge/port-5001-green)

## Tools

### Organise
| Tool | Description |
|------|-------------|
| **Merge PDF** | Combine multiple PDFs into one file — drag to set order |
| **Split PDF** | Separate every page into its own individual PDF |
| **Remove Pages** | Delete specific pages by number or range (e.g. `2, 4, 6-8`) |
| **Extract Pages** | Save a selection of pages as a new PDF (e.g. `1-3, 5`) |
| **Organize PDF** | Drag and drop page thumbnails to reorder them |
| **Rotate PDF** | Rotate all pages 90°, 180°, or 270° |
| **Crop PDF** | Trim margins from every page (top, bottom, left, right in mm) |
| **Page Numbers** | Add page numbers — choose bottom-centre, bottom-right, bottom-left, or top-centre |
| **Watermark** | Stamp custom text diagonally across every page |

### Optimise
| Tool | Description |
|------|-------------|
| **Compress PDF** | Reduce file size with 4 levels: Lossless, Balanced (150 DPI), High quality (300 DPI), Maximum (72 DPI) |
| **Repair PDF** | Attempt to fix corrupt or damaged PDF files using Ghostscript |
| **OCR PDF** | Make scanned PDFs searchable and copy-able using Tesseract OCR |

### Convert
| Tool | Description |
|------|-------------|
| **PDF to JPG** | Export every page as a JPG image — choose 96, 150, 200 or 300 DPI |
| **JPG to PDF** | Combine one or more images (JPG, PNG, WEBP, BMP) into a PDF |
| **Word to PDF** | Convert `.docx` / `.doc` files to PDF via LibreOffice |
| **PDF to Word** | Convert PDF to an editable `.docx` file |
| **Excel to PDF** | Convert `.xlsx` / `.xls` / `.ods` spreadsheets to PDF via LibreOffice |
| **PowerPoint to PDF** | Convert `.pptx` / `.ppt` presentations to PDF via LibreOffice |
| **HTML to PDF** | Convert pasted HTML or a webpage URL to PDF via WeasyPrint |
| **PDF to PDF/A** | Convert to archival PDF/A-2 format using Ghostscript |

### Security
| Tool | Description |
|------|-------------|
| **Protect PDF** | Encrypt with AES-256 password protection |
| **Unlock PDF** | Remove password protection (requires the current password) |
| **Redact PDF** | Permanently black out text phrases — provide comma-separated terms |
| **Sign PDF** | Draw a signature on a canvas and stamp it onto a chosen page |

## Tech Stack

| Component | Technology |
|-----------|------------|
| Backend | Python / Flask |
| PDF engine | PyMuPDF (fitz) |
| Encryption | pikepdf |
| Compression | Ghostscript |
| Office conversion | LibreOffice headless |
| OCR | Tesseract + pdf2image |
| HTML to PDF | WeasyPrint |
| Image to PDF | img2pdf + Pillow |
| PDF to Word | pdf2docx |
| Frontend | Vanilla HTML/CSS/JS |
| Container | Docker |

## Privacy

No files are saved to the server. All processing happens in memory (or short-lived temp directories for external tools) and the result is streamed directly to your browser. Once you close the tab, nothing remains.

## Quick Start

### Requirements
- Docker Desktop installed and running

### Run

```bash
git clone https://github.com/sham4n85/pdf-tools.git
cd pdf-tools
docker compose up -d --build
```

App is available at **http://localhost:5001**

First build takes 5–10 minutes (downloads LibreOffice, Tesseract, Ghostscript etc.). Subsequent starts are instant.

### Stop

```bash
docker compose down
```
