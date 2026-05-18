import io, os, zipfile, subprocess, tempfile, threading, base64
from flask import Flask, render_template, request, jsonify, send_file
import fitz          # PyMuPDF
import pikepdf
import img2pdf
from PIL import Image

_lo_lock = threading.Lock()   # LibreOffice is not safe to run concurrently


def libreoffice_convert(data, filename):
    """Convert any Office document to PDF via LibreOffice headless."""
    with _lo_lock:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, filename)
            with open(src, 'wb') as f:
                f.write(data)
            env = {**os.environ, 'HOME': tmp, 'TMPDIR': tmp}
            r = subprocess.run(
                ['libreoffice', '--headless', '--convert-to', 'pdf',
                 '--outdir', tmp, src],
                capture_output=True, timeout=120, env=env
            )
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode('utf-8', errors='replace') or 'Conversion failed')
            pdfs = [f for f in os.listdir(tmp) if f.endswith('.pdf')]
            if not pdfs:
                raise RuntimeError('LibreOffice produced no output')
            with open(os.path.join(tmp, pdfs[0]), 'rb') as f:
                return f.read()


def parse_page_ranges(page_str, total):
    """Parse '1,3,5-7' into a sorted list of 0-indexed page numbers."""
    pages = set()
    for part in page_str.split(','):
        part = part.strip()
        if not part:
            continue
        if '-' in part:
            a, b = part.split('-', 1)
            try:
                pages.update(range(int(a) - 1, int(b)))
            except ValueError:
                pass
        else:
            try:
                pages.add(int(part) - 1)
            except ValueError:
                pass
    return sorted(p for p in pages if 0 <= p < total)


def _gs_run(data, extra_args, timeout=120):
    """Run Ghostscript on PDF bytes with extra args and return output bytes."""
    with tempfile.TemporaryDirectory() as tmp:
        inp = os.path.join(tmp, 'in.pdf')
        out = os.path.join(tmp, 'out.pdf')
        with open(inp, 'wb') as f:
            f.write(data)
        r = subprocess.run(
            ['gs', '-sDEVICE=pdfwrite', '-dNOPAUSE', '-dQUIET', '-dBATCH']
            + extra_args + [f'-sOutputFile={out}', inp],
            capture_output=True, timeout=timeout
        )
        if r.returncode != 0:
            raise RuntimeError(r.stderr.decode('utf-8', errors='replace'))
        with open(out, 'rb') as f:
            return f.read()


app = Flask(__name__)
app.config['MAX_CONTENT_LENGTH'] = 200 * 1024 * 1024  # 200 MB


def send_pdf(buf, name):
    buf.seek(0)
    return send_file(buf, mimetype='application/pdf',
                     as_attachment=True, download_name=name)


def send_zip(buf, name):
    buf.seek(0)
    return send_file(buf, mimetype='application/zip',
                     as_attachment=True, download_name=name)


# ── Routes ─────────────────────────────────────────────────────────────────────

@app.route('/')
def index():
    return render_template('index.html')


# ── Organise ───────────────────────────────────────────────────────────────────

@app.route('/api/merge', methods=['POST'])
def merge():
    files = request.files.getlist('files')
    if len(files) < 2:
        return jsonify({'error': 'Select at least 2 PDF files'}), 400
    result = fitz.open()
    for f in files:
        doc = fitz.open(stream=f.read(), filetype='pdf')
        result.insert_pdf(doc)
        doc.close()
    buf = io.BytesIO()
    result.save(buf, garbage=3, deflate=True)
    result.close()
    return send_pdf(buf, 'merged.pdf')


@app.route('/api/split', methods=['POST'])
def split():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    doc = fitz.open(stream=f.read(), filetype='pdf')
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i in range(len(doc)):
            pg = fitz.open()
            pg.insert_pdf(doc, from_page=i, to_page=i)
            pb = io.BytesIO()
            pg.save(pb)
            pg.close()
            zf.writestr(f'page_{i+1:03d}.pdf', pb.getvalue())
    doc.close()
    return send_zip(buf, 'split_pages.zip')


@app.route('/api/remove-pages', methods=['POST'])
def remove_pages():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    pages_str = request.form.get('pages', '').strip()
    if not pages_str:
        return jsonify({'error': 'Specify page numbers to remove (e.g. 2, 4, 6-8)'}), 400
    doc = fitz.open(stream=f.read(), filetype='pdf')
    to_remove = parse_page_ranges(pages_str, len(doc))
    if not to_remove:
        return jsonify({'error': 'No valid page numbers specified'}), 400
    if len(to_remove) >= len(doc):
        return jsonify({'error': 'Cannot remove all pages from a PDF'}), 400
    for p in sorted(to_remove, reverse=True):
        doc.delete_page(p)
    buf = io.BytesIO()
    doc.save(buf, garbage=3, deflate=True)
    doc.close()
    return send_pdf(buf, 'removed.pdf')


@app.route('/api/extract-pages', methods=['POST'])
def extract_pages():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    pages_str = request.form.get('pages', '').strip()
    if not pages_str:
        return jsonify({'error': 'Specify pages to extract (e.g. 1-3, 5)'}), 400
    doc = fitz.open(stream=f.read(), filetype='pdf')
    to_extract = parse_page_ranges(pages_str, len(doc))
    if not to_extract:
        return jsonify({'error': 'No valid page numbers specified'}), 400
    result = fitz.open()
    for p in to_extract:
        result.insert_pdf(doc, from_page=p, to_page=p)
    buf = io.BytesIO()
    result.save(buf, garbage=3, deflate=True)
    result.close()
    doc.close()
    return send_pdf(buf, 'extracted.pdf')


@app.route('/api/thumbnails', methods=['POST'])
def thumbnails():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    doc = fitz.open(stream=f.read(), filetype='pdf')
    thumbs = []
    for i, page in enumerate(doc):
        mat = fitz.Matrix(0.25, 0.25)
        pix = page.get_pixmap(matrix=mat)
        b64 = base64.b64encode(pix.tobytes('png')).decode()
        thumbs.append({'page': i + 1, 'data': f'data:image/png;base64,{b64}'})
    doc.close()
    return jsonify({'count': len(thumbs), 'thumbnails': thumbs})


@app.route('/api/organize', methods=['POST'])
def organize():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    order_str = request.form.get('order', '').strip()
    if not order_str:
        return jsonify({'error': 'No page order provided'}), 400
    try:
        order = [int(x) - 1 for x in order_str.split(',')]
    except ValueError:
        return jsonify({'error': 'Invalid page order'}), 400
    doc = fitz.open(stream=f.read(), filetype='pdf')
    total = len(doc)
    if any(p < 0 or p >= total for p in order):
        return jsonify({'error': 'Page number out of range'}), 400
    result = fitz.open()
    for p in order:
        result.insert_pdf(doc, from_page=p, to_page=p)
    buf = io.BytesIO()
    result.save(buf, garbage=3, deflate=True)
    result.close()
    doc.close()
    return send_pdf(buf, 'organized.pdf')


@app.route('/api/rotate', methods=['POST'])
def rotate():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    angle = int(request.form.get('angle', 90))
    doc = fitz.open(stream=f.read(), filetype='pdf')
    for page in doc:
        page.set_rotation((page.rotation + angle) % 360)
    buf = io.BytesIO()
    doc.save(buf, garbage=3, deflate=True)
    doc.close()
    return send_pdf(buf, 'rotated.pdf')


@app.route('/api/crop', methods=['POST'])
def crop():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    mm2pt = 2.83465
    top    = float(request.form.get('top',    0)) * mm2pt
    bottom = float(request.form.get('bottom', 0)) * mm2pt
    left   = float(request.form.get('left',   0)) * mm2pt
    right  = float(request.form.get('right',  0)) * mm2pt
    doc = fitz.open(stream=f.read(), filetype='pdf')
    for page in doc:
        r = page.rect
        crop_rect = fitz.Rect(r.x0 + left, r.y0 + top, r.x1 - right, r.y1 - bottom)
        if crop_rect.is_valid and crop_rect.width > 10 and crop_rect.height > 10:
            page.set_cropbox(crop_rect)
    buf = io.BytesIO()
    doc.save(buf, garbage=3, deflate=True)
    doc.close()
    return send_pdf(buf, 'cropped.pdf')


@app.route('/api/watermark', methods=['POST'])
def watermark():
    f = request.files.get('file')
    text = request.form.get('text', 'CONFIDENTIAL').strip()
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    if not text:
        return jsonify({'error': 'Watermark text is required'}), 400
    doc = fitz.open(stream=f.read(), filetype='pdf')
    for page in doc:
        w, h = page.rect.width, page.rect.height
        page.insert_text(
            fitz.Point(w * 0.15, h * 0.55),
            text,
            fontsize=min(w, h) * 0.08,
            color=(0.75, 0.75, 0.75),
            rotate=45,
            overlay=True,
        )
    buf = io.BytesIO()
    doc.save(buf, garbage=3, deflate=True)
    doc.close()
    return send_pdf(buf, 'watermarked.pdf')


@app.route('/api/page-numbers', methods=['POST'])
def page_numbers():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    position = request.form.get('position', 'bottom-center')
    doc = fitz.open(stream=f.read(), filetype='pdf')
    total = len(doc)
    for i, page in enumerate(doc):
        w, h = page.rect.width, page.rect.height
        label = f'{i+1} / {total}'
        pos_map = {
            'bottom-center': fitz.Point(w/2 - 20, h - 20),
            'bottom-right':  fitz.Point(w - 50,   h - 20),
            'bottom-left':   fitz.Point(20,        h - 20),
            'top-center':    fitz.Point(w/2 - 20,  20),
        }
        pt = pos_map.get(position, pos_map['bottom-center'])
        page.insert_text(pt, label, fontsize=10, color=(0, 0, 0), overlay=True)
    buf = io.BytesIO()
    doc.save(buf, garbage=3, deflate=True)
    doc.close()
    return send_pdf(buf, 'numbered.pdf')


# ── Optimise ───────────────────────────────────────────────────────────────────

GS_SETTINGS = {
    'lossless': None,
    'high':     '/printer',
    'balanced': '/ebook',
    'maximum':  '/screen',
}


@app.route('/api/compress', methods=['POST'])
def compress():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    level    = request.form.get('level', 'balanced')
    original = f.read()
    if level == 'lossless' or level not in GS_SETTINGS:
        doc = fitz.open(stream=original, filetype='pdf')
        buf = io.BytesIO()
        doc.save(buf, garbage=4, deflate=True, clean=True,
                 deflate_images=True, deflate_fonts=True)
        doc.close()
        result = buf.getvalue()
    else:
        try:
            compressed = _gs_run(original, ['-dCompatibilityLevel=1.5',
                                            f'-dPDFSETTINGS={GS_SETTINGS[level]}'])
            result = compressed if len(compressed) < len(original) else original
        except Exception as e:
            return jsonify({'error': f'Ghostscript error: {e}'}), 500
    resp = send_pdf(io.BytesIO(result), 'compressed.pdf')
    resp.headers['X-Original-Size']   = str(len(original))
    resp.headers['X-Compressed-Size'] = str(len(result))
    return resp


@app.route('/api/repair', methods=['POST'])
def repair():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        result = _gs_run(f.read(), ['-dCompatibilityLevel=1.5'])
        return send_pdf(io.BytesIO(result), 'repaired.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ocr', methods=['POST'])
def ocr_pdf():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        import pytesseract
        from pdf2image import convert_from_bytes
        images = convert_from_bytes(f.read(), dpi=200)
        result_doc = fitz.open()
        for img in images:
            page_pdf = pytesseract.image_to_pdf_or_hocr(img, extension='pdf')
            page_doc = fitz.open(stream=page_pdf, filetype='pdf')
            result_doc.insert_pdf(page_doc)
            page_doc.close()
        buf = io.BytesIO()
        result_doc.save(buf, garbage=3, deflate=True)
        result_doc.close()
        return send_pdf(buf, 'searchable.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Convert ────────────────────────────────────────────────────────────────────

@app.route('/api/pdf-to-jpg', methods=['POST'])
def pdf_to_jpg():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    dpi = min(int(request.form.get('dpi', 150)), 300)
    doc = fitz.open(stream=f.read(), filetype='pdf')
    scale = dpi / 72
    mat = fitz.Matrix(scale, scale)
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for i, page in enumerate(doc):
            pix = page.get_pixmap(matrix=mat)
            zf.writestr(f'page_{i+1:03d}.jpg', pix.tobytes('jpeg'))
    doc.close()
    return send_zip(buf, 'pdf_images.zip')


@app.route('/api/jpg-to-pdf', methods=['POST'])
def jpg_to_pdf():
    files = request.files.getlist('files')
    if not files:
        return jsonify({'error': 'No images uploaded'}), 400
    images = []
    for f in files:
        img = Image.open(io.BytesIO(f.read()))
        if img.mode not in ('RGB', 'L'):
            img = img.convert('RGB')
        b = io.BytesIO()
        img.save(b, 'JPEG', quality=92)
        images.append(b.getvalue())
    pdf = img2pdf.convert(images)
    return send_pdf(io.BytesIO(pdf), 'images.pdf')


@app.route('/api/word-to-pdf', methods=['POST'])
def word_to_pdf():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        pdf = libreoffice_convert(f.read(), f.filename or 'document.docx')
        return send_pdf(io.BytesIO(pdf), 'document.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/pdf-to-word', methods=['POST'])
def pdf_to_word():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        from pdf2docx import Converter
        with tempfile.TemporaryDirectory() as tmp:
            pdf_path  = os.path.join(tmp, 'input.pdf')
            docx_path = os.path.join(tmp, 'output.docx')
            with open(pdf_path, 'wb') as fp:
                fp.write(f.read())
            cv = Converter(pdf_path)
            cv.convert(docx_path)
            cv.close()
            with open(docx_path, 'rb') as fp:
                docx = fp.read()
        mime = 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'
        buf = io.BytesIO(docx)
        buf.seek(0)
        return send_file(buf, mimetype=mime, as_attachment=True, download_name='document.docx')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/excel-to-pdf', methods=['POST'])
def excel_to_pdf():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        pdf = libreoffice_convert(f.read(), f.filename or 'spreadsheet.xlsx')
        return send_pdf(io.BytesIO(pdf), 'spreadsheet.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/ppt-to-pdf', methods=['POST'])
def ppt_to_pdf():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        pdf = libreoffice_convert(f.read(), f.filename or 'presentation.pptx')
        return send_pdf(io.BytesIO(pdf), 'presentation.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/html-to-pdf', methods=['POST'])
def html_to_pdf():
    html_content = request.form.get('html', '').strip()
    url = request.form.get('url', '').strip()
    if not html_content and not url:
        return jsonify({'error': 'Provide HTML content or a URL'}), 400
    try:
        from weasyprint import HTML
        if url:
            pdf_bytes = HTML(url=url).write_pdf()
        else:
            pdf_bytes = HTML(string=html_content).write_pdf()
        return send_pdf(io.BytesIO(pdf_bytes), 'document.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@app.route('/api/pdf-to-pdfa', methods=['POST'])
def pdf_to_pdfa():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    data = f.read()
    try:
        with tempfile.TemporaryDirectory() as tmp:
            inp = os.path.join(tmp, 'in.pdf')
            out = os.path.join(tmp, 'out.pdf')
            with open(inp, 'wb') as fp:
                fp.write(data)
            r = subprocess.run(
                ['gs', '-dBATCH', '-dNOPAUSE', '-dQUIET',
                 '-dPDFA=2', '-dPDFACompatibilityPolicy=1',
                 '-sDEVICE=pdfwrite',
                 '-sColorConversionStrategy=UseDeviceIndependentColor',
                 f'-sOutputFile={out}', inp],
                capture_output=True, timeout=120
            )
            if r.returncode != 0:
                raise RuntimeError(r.stderr.decode('utf-8', errors='replace') or 'PDF/A conversion failed')
            with open(out, 'rb') as fp:
                result = fp.read()
        return send_pdf(io.BytesIO(result), 'archive.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Security ───────────────────────────────────────────────────────────────────

@app.route('/api/protect', methods=['POST'])
def protect():
    f = request.files.get('file')
    password = request.form.get('password', '').strip()
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    if not password:
        return jsonify({'error': 'Password is required'}), 400
    pdf = pikepdf.open(io.BytesIO(f.read()))
    buf = io.BytesIO()
    pdf.save(buf, encryption=pikepdf.Encryption(
        owner=password, user=password, R=6,
        allow=pikepdf.Permissions(extract=False, modify_annotation=False)
    ))
    pdf.close()
    return send_pdf(buf, 'protected.pdf')


@app.route('/api/unlock', methods=['POST'])
def unlock():
    f = request.files.get('file')
    password = request.form.get('password', '').strip()
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    try:
        pdf = pikepdf.open(io.BytesIO(f.read()), password=password)
        buf = io.BytesIO()
        pdf.save(buf)
        pdf.close()
        return send_pdf(buf, 'unlocked.pdf')
    except pikepdf.PasswordError:
        return jsonify({'error': 'Incorrect password'}), 400
    except Exception as e:
        return jsonify({'error': str(e)}), 400


@app.route('/api/redact', methods=['POST'])
def redact():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    text = request.form.get('text', '').strip()
    if not text:
        return jsonify({'error': 'Specify text phrases to redact (comma-separated)'}), 400
    terms = [t.strip() for t in text.split(',') if t.strip()]
    doc = fitz.open(stream=f.read(), filetype='pdf')
    for page in doc:
        for term in terms:
            for rect in page.search_for(term):
                page.add_redact_annot(rect, fill=(0, 0, 0))
        page.apply_redactions()
    buf = io.BytesIO()
    doc.save(buf, garbage=3, deflate=True)
    doc.close()
    return send_pdf(buf, 'redacted.pdf')


@app.route('/api/sign', methods=['POST'])
def sign():
    f = request.files.get('file')
    if not f:
        return jsonify({'error': 'No file uploaded'}), 400
    sig_data = request.form.get('signature', '').strip()
    if not sig_data:
        return jsonify({'error': 'No signature provided — draw one first'}), 400
    page_num = max(0, int(request.form.get('page', 1)) - 1)
    try:
        if ',' in sig_data:
            sig_data = sig_data.split(',', 1)[1]
        sig_bytes = base64.b64decode(sig_data)
        doc = fitz.open(stream=f.read(), filetype='pdf')
        page_num = min(page_num, len(doc) - 1)
        page = doc[page_num]
        pw, ph = page.rect.width, page.rect.height
        # Place at bottom-centre
        sig_w = pw * 0.4
        sig_h = ph * 0.1
        x = (pw - sig_w) / 2
        y = ph - sig_h - 30
        page.insert_image(fitz.Rect(x, y, x + sig_w, y + sig_h), stream=sig_bytes)
        buf = io.BytesIO()
        doc.save(buf, garbage=3, deflate=True)
        doc.close()
        return send_pdf(buf, 'signed.pdf')
    except Exception as e:
        return jsonify({'error': str(e)}), 500


# ── Error handlers ─────────────────────────────────────────────────────────────

@app.errorhandler(413)
def too_large(e):
    return jsonify({'error': 'File too large (max 200 MB)'}), 413


if __name__ == '__main__':
    app.run(host='0.0.0.0', port=5001, threaded=True)
