import os, re, io, csv, json, uuid, zipfile, random, tempfile, shutil, glob
from pathlib import Path
from collections import defaultdict, Counter
from flask import Flask, request, render_template, jsonify, send_file, send_from_directory, session, redirect, url_for, flash
from flask_session import Session
import pandas as pd
import opendataloader_pdf

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'edutools-dev-secret-2026')
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024
app.config['SESSION_TYPE'] = 'filesystem'
app.config['SESSION_FILE_DIR'] = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'flask_session')
os.makedirs(app.config['SESSION_FILE_DIR'], exist_ok=True)
Session(app)

# ── Assessment Generator constants ──────────────────────────────────────────
ASSESSMENT_TYPES = {
    'homework': 'Homework', 'classwork': 'Classwork',
    'class test': 'ClassTest', 'classtest': 'ClassTest',
    'project': 'Project', 'mcq': 'MCQ', 'essay': 'Essay',
    'mixed': 'Mixed', 'orals': 'Orals', 'oral': 'Orals',
}
ALL_TYPES = ['Homework', 'Classwork', 'ClassTest', 'Project', 'MCQ', 'Essay', 'Mixed', 'Orals']
OUTPUT_COLUMNS = [
    'AdmissionNo', 'StudentName', 'Class', 'SubjectID',
    'Homework', 'Classwork', 'ClassTest', 'Project',
    'Attendance', 'Participation', 'Effort', 'Qualitywork',
    'Attitude', 'Performance', 'MCQ', 'Essay', 'Mixed', 'Orals'
]
BEHAVIOURAL_COLS = ['Attendance', 'Participation', 'Effort', 'Qualitywork', 'Attitude', 'Performance']
COLUMN_RE = re.compile(r'^(\d+)\.(\d+)\.(\d+)\.([\d/]+)\s*[-\u2013]?\s*(.+)$')
META_COLS = {'surname', 'last name', 'first name', 'firstname', 'given name', 'email', 'email address', 'id', 'student id', ''}

_store = {}
_dl_store = {}


# ── Shared helpers ───────────────────────────────────────────────────────────
def fix_name(word):
    return '-'.join(p.capitalize() for p in word.split('-'))


# ── Assessment helpers ───────────────────────────────────────────────────────
def detect_atype(label):
    lower = label.strip().lower()
    for key in sorted(ASSESSMENT_TYPES, key=len, reverse=True):
        if lower.startswith(key):
            return ASSESSMENT_TYPES[key]
    return None


def read_csv_assessment(file_bytes):
    text = file_bytes.decode('utf-8-sig')
    reader = csv.reader(io.StringIO(text))
    headers = None
    rows = []
    skip = {'date', 'points', 'range'}
    for row in reader:
        if headers is None:
            headers = row
            continue
        if row and row[0].strip().lower() in skip:
            continue
        rows.append(row)
    return headers, rows


def parse_columns(headers):
    parsed, ns = [], []
    parsed_indices = set()
    for i, h in enumerate(headers):
        m = COLUMN_RE.match(h.strip())
        if m:
            term, week, lesson, year, label = m.groups()
            atype = detect_atype(label)
            is_prog = label.strip().lower().startswith('progressive')
            cat = 'progressive' if is_prog else ('standard' if atype else 'ambiguous')
            parsed.append({
                'original': h.strip(), 'index': i,
                'term': int(term), 'week': int(week),
                'lesson': int(lesson), 'year': year.strip(),
                'label': label.strip(), 'atype': atype,
                'category': cat,
            })
            parsed_indices.add(i)
    for i, h in enumerate(headers):
        if i not in parsed_indices and h.strip().lower() not in META_COLS:
            ns.append({'original': h.strip(), 'index': i, 'label': h.strip(), 'category': 'nonstandard'})
    return parsed, ns


# ── Routes ───────────────────────────────────────────────────────────────────
@app.route('/')
def index():
    return render_template('index.html')


# ── Group Generator ──────────────────────────────────────────────────────────
@app.route('/group-generator')
def group_generator():
    return render_template('group_generator.html')


@app.route('/upload', methods=['POST'])
def upload():
    try:
        if 'file' not in request.files:
            return jsonify({'error': 'No file provided'}), 400
        file = request.files['file']
        if file.filename == '' or not file.filename.lower().endswith('.csv'):
            return jsonify({'error': 'Please upload a valid CSV file'}), 400

        stream = io.StringIO(file.stream.read().decode("UTF8"), newline=None)
        csv_reader = csv.reader(stream)
        students = []
        for row in csv_reader:
            if not row or len(row) < 4:
                continue
            student_id = row[0].strip()
            surname = row[1].strip()
            first_names = row[2].strip()
            sex = row[3].strip().upper()
            if not surname or not first_names or sex not in ['M', 'F']:
                continue
            full_name = f"{surname} {first_names}".strip()
            students.append({"name": full_name, "sex": sex, "id": student_id})

        if not students:
            return jsonify({'error': 'No valid students found. Ensure Sex column contains only M or F.'}), 400
        return jsonify({'success': True, 'students': students, 'count': len(students)})
    except Exception as e:
        return jsonify({'error': f'Error processing file: {str(e)}'}), 500


@app.route('/generate-groups', methods=['POST'])
def generate_groups():
    try:
        data = request.get_json()
        students = data.get('students', [])
        num_groups = int(data.get('num_groups', 2))
        create_extra_group = data.get('create_extra_group', False)

        if not students:
            return jsonify({'error': 'No students loaded'}), 400

        total = len(students)
        max_allowed_groups = total // 2

        if num_groups < 1 or num_groups > max_allowed_groups:
            return jsonify({'error': f'Number of groups must be between 1 and {max_allowed_groups}'}), 400

        base_size = total // num_groups
        remainder = total % num_groups
        random.shuffle(students)
        students_sorted = sorted(students, key=lambda x: x['sex'])
        groups = [[] for _ in range(num_groups)]
        for i, student in enumerate(students_sorted):
            groups[i % num_groups].append(student)

        note = ""
        if create_extra_group and remainder > 0:
            threshold = max(1, (base_size * 2) // 3)
            if remainder >= threshold:
                extra_group = []
                for _ in range(remainder):
                    largest = max(range(num_groups), key=lambda x: len(groups[x]))
                    extra_group.append(groups[largest].pop())
                groups.append(extra_group)
                note = f"Extra group created with {remainder} surplus members"
        elif remainder > 0:
            extra_students = []
            for _ in range(remainder):
                largest = max(range(num_groups), key=lambda x: len(groups[x]))
                extra_students.append(groups[largest].pop())
            random.shuffle(extra_students)
            for i in range(remainder):
                groups[i].append(extra_students[i])

        for group in groups:
            random.shuffle(group)

        formatted = []
        for i, group in enumerate(groups, 1):
            males = sum(1 for s in group if s['sex'] == 'M')
            females = len(group) - males
            formatted.append({
                'number': i, 'size': len(group),
                'males': males, 'females': females,
                'members': [s['name'] for s in group]
            })

        return jsonify({'success': True, 'groups': formatted, 'note': note})
    except Exception as e:
        return jsonify({'error': 'Error generating groups. Please try again.'}), 500


# ── Student Picker ───────────────────────────────────────────────────────────
@app.route('/student-picker')
def student_picker():
    return render_template('student_picker.html')


# ── Assessment Generator ─────────────────────────────────────────────────────
@app.route('/assessment-generator')
def assessment_generator():
    return render_template('assessment.html')


@app.route('/parse', methods=['POST'])
def parse_file():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    f = request.files['file']
    if not f.filename.lower().endswith('.csv'):
        return jsonify({'error': 'Please upload a CSV file'}), 400

    try:
        headers, rows = read_csv_assessment(f.read())
    except Exception as e:
        return jsonify({'error': str(e)}), 400

    parsed_cols, ns_cols = parse_columns(headers)
    if not parsed_cols and not ns_cols:
        return jsonify({'error': 'No assessment columns detected'}), 400

    file_key = str(uuid.uuid4())
    _store[file_key] = (headers, rows)

    email_idx = next((i for i, h in enumerate(headers) if 'email' in h.lower()), None)
    sur_idx = next((i for i, h in enumerate(headers) if h.strip().lower() in ('surname', 'last name')), 0)
    fn_idx  = next((i for i, h in enumerate(headers) if h.strip().lower() in ('first name', 'firstname')), 1)

    preview_idx = [sur_idx, fn_idx]
    all_cols_for_preview = parsed_cols + ns_cols
    for c in all_cols_for_preview:
        if c['index'] not in preview_idx:
            preview_idx.append(c['index'])

    col_cat_map = {}
    for c in parsed_cols: col_cat_map[c['index']] = c['category']
    for c in ns_cols:      col_cat_map[c['index']] = 'nonstandard'
    col_cat_map[sur_idx] = col_cat_map[fn_idx] = 'meta'

    preview_headers = [headers[i].strip() for i in preview_idx]
    preview_cats    = [col_cat_map.get(i, 'meta') for i in preview_idx]
    preview_rows    = []
    for row in rows:
        if not any(c.strip() for c in row):
            continue
        preview_rows.append([row[i].strip() if i < len(row) else '' for i in preview_idx])

    name_parts = os.path.splitext(f.filename)[0].split('_')
    inferred_subject = f"{name_parts[0]}-{name_parts[1]}" if len(name_parts) >= 2 else ''
    inferred_class   = name_parts[2] if len(name_parts) >= 3 else ''

    return jsonify({
        'file_key': file_key, 'filename': f.filename,
        'student_count': len(preview_rows), 'column_count': len(parsed_cols),
        'standard_columns':    [c for c in parsed_cols if c['category'] == 'standard'],
        'progressive_columns': [c for c in parsed_cols if c['category'] == 'progressive'],
        'ambiguous_columns':   [c for c in parsed_cols if c['category'] == 'ambiguous'],
        'nonstandard_columns': ns_cols,
        'preview': {'headers': preview_headers, 'rows': preview_rows, 'cats': preview_cats},
        'years': sorted({c['year'] for c in parsed_cols}),
        'terms': sorted({c['term'] for c in parsed_cols}),
        'weeks': sorted({c['week'] for c in parsed_cols}),
        'class_name': inferred_class, 'subject_id': inferred_subject,
    })


@app.route('/generate', methods=['POST'])
def generate():
    data = request.json
    file_key = data.get('file_key')
    if file_key not in _store:
        return jsonify({'error': 'Session expired. Please re-upload.'}), 400

    headers, rows = _store[file_key]
    class_name  = data.get('class_name', '')
    subject_id  = data.get('subject_id', '')
    target_weeks = [int(w) for w in data.get('weeks', [])]
    column_map   = data.get('column_map', [])

    if not target_weeks:
        return jsonify({'error': 'No weeks selected'}), 400

    email_idx = next((i for i, h in enumerate(headers) if 'email' in h.lower()), None)
    sur_idx   = next((i for i, h in enumerate(headers) if h.strip().lower() in ('surname', 'last name')), 0)
    fn_idx    = next((i for i, h in enumerate(headers) if h.strip().lower() in ('first name', 'firstname')), 1)

    week_type_map = defaultdict(lambda: defaultdict(list))
    for cm in column_map:
        if cm.get('action') == 'skip':
            continue
        week_type_map[int(cm['output_week'])][cm['output_type']].append(cm)

    generated = []
    for week in target_weeks:
        type_cols = week_type_map.get(week, {})
        output_rows = []

        for row in rows:
            if not any(c.strip() for c in row):
                continue
            admission_no = ''
            if email_idx is not None and email_idx < len(row):
                email = row[email_idx].strip()
                admission_no = email.split('@')[0] if '@' in email else email

            surname   = row[sur_idx].strip() if sur_idx < len(row) else ''
            firstname = row[fn_idx].strip()  if fn_idx  < len(row) else ''
            student_name = f"{' '.join(fix_name(w) for w in surname.split())} {firstname}".strip()

            out = {'AdmissionNo': admission_no, 'StudentName': student_name,
                   'Class': class_name, 'SubjectID': subject_id}

            for atype in ALL_TYPES:
                cms = type_cols.get(atype, [])
                if not cms:
                    out[atype] = ''
                    continue
                conflict_action = cms[0].get('conflict_action', 'first')
                if conflict_action == 'sum':
                    total = 0
                    for cm in cms:
                        raw = row[cm['source_index']].strip() if cm['source_index'] < len(row) else ''
                        try: total += float(raw) if raw else 0
                        except ValueError: pass
                    out[atype] = str(int(total))
                else:
                    raw = row[cms[0]['source_index']].strip() if cms[0]['source_index'] < len(row) else ''
                    try: out[atype] = str(int(float(raw))) if raw else '0'
                    except ValueError: out[atype] = '0'

            for b in BEHAVIOURAL_COLS:
                out[b] = '1'
            output_rows.append(out)

        output_rows.sort(key=lambda r: r['StudentName'].lower())

        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=OUTPUT_COLUMNS)
        w.writeheader()
        w.writerows(output_rows)

        base = subject_id.replace('-', '_')
        filename = f"{base}_{class_name}_Week_{week}.csv"
        dl_key = str(uuid.uuid4())
        _dl_store[dl_key] = (buf.getvalue(), filename)
        generated.append({'key': dl_key, 'filename': filename, 'students': len(output_rows), 'week': week})

    return jsonify({'files': generated})


@app.route('/download/<key>')
def download(key):
    result = _dl_store.get(key)
    if not result:
        return 'Not found', 404
    content, filename = result
    buf = io.BytesIO(content.encode('utf-8-sig'))
    return send_file(buf, mimetype='text/csv', as_attachment=True, download_name=filename)


@app.route('/download-zip', methods=['POST'])
def download_zip():
    keys = request.json.get('keys', [])
    if not keys:
        return 'No keys provided', 400
    zip_buf = io.BytesIO()
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for key in keys:
            result = _dl_store.get(key)
            if result:
                content, filename = result
                zf.writestr(filename, content.encode('utf-8-sig'))
    zip_buf.seek(0)
    return send_file(zip_buf, mimetype='application/zip', as_attachment=True, download_name='weekly_assessments.zip')


# ── PDF Extractor ────────────────────────────────────────────────────────────
ALLOWED_FORMATS = {'markdown', 'json', 'html', 'text'}
FORMAT_EXT = {'markdown': '.md', 'json': '.json', 'html': '.html', 'text': '.txt'}

# Persistent extraction storage: key -> { 'out_dir': str, 'main_file': str,
#   'content': str, 'filename': str, 'format': str, 'images': list, 'image_dir': str|None }
_extraction_store = {}


def _cleanup_extraction(key):
    entry = _extraction_store.pop(key, None)
    if entry and entry.get('tmp_dir'):
        shutil.rmtree(entry['tmp_dir'], ignore_errors=True)


@app.route('/pdf-extractor')
def pdf_extractor():
    return render_template('pdf_extractor.html')


@app.route('/extract-pdf', methods=['POST'])
def extract_pdf():
    if 'file' not in request.files:
        return jsonify({'error': 'No file provided'}), 400
    f = request.files['file']
    if f.filename == '' or not f.filename.lower().endswith('.pdf'):
        return jsonify({'error': 'Please upload a valid PDF file'}), 400

    out_format = request.form.get('format', 'markdown')
    if out_format not in ALLOWED_FORMATS:
        return jsonify({'error': f'Invalid format. Choose from: {", ".join(ALLOWED_FORMATS)}'}), 400

    tmp_dir = tempfile.mkdtemp()
    try:
        # Save uploaded PDF
        safe_name = os.path.basename(f.filename)
        pdf_path = os.path.join(tmp_dir, safe_name)
        f.save(pdf_path)

        out_dir = os.path.join(tmp_dir, 'output')
        os.makedirs(out_dir)

        # Extract with external images (files on disk)
        opendataloader_pdf.convert(
            input_path=[pdf_path],
            output_dir=out_dir,
            format=out_format,
            image_output='external',
            quiet=True,
        )

        # Find the main output file
        ext = FORMAT_EXT[out_format]
        output_files = glob.glob(os.path.join(out_dir, '**', f'*{ext}'), recursive=True)
        if not output_files:
            shutil.rmtree(tmp_dir, ignore_errors=True)
            return jsonify({'error': 'Extraction produced no output. Is Java 11+ installed?'}), 500

        main_file = output_files[0]
        with open(main_file, 'r', encoding='utf-8') as fh:
            content = fh.read()

        # Discover extracted images
        image_files = []
        image_dir_name = None
        for d in sorted(Path(out_dir).rglob('*')):
            if d.is_file() and d.suffix.lower() in ('.png', '.jpg', '.jpeg', '.gif', '.webp', '.svg'):
                rel = str(d.relative_to(out_dir))
                image_files.append(rel)
                if image_dir_name is None:
                    image_dir_name = str(d.parent.relative_to(out_dir))

        # Build a session key and persist the output
        extraction_key = str(uuid.uuid4())
        stem = os.path.splitext(safe_name)[0]
        dl_filename = f"{stem}{ext}"

        _extraction_store[extraction_key] = {
            'tmp_dir': tmp_dir,
            'out_dir': out_dir,
            'main_file': main_file,
            'content': content,
            'filename': dl_filename,
            'format': out_format,
            'images': image_files,
            'image_dir': image_dir_name,
        }

        # Also store main file in _dl_store for single-file download
        _dl_store[extraction_key] = (content, dl_filename)

        # Build preview content: rewrite image paths to point at our serve route
        preview_content = content
        if image_files:
            for img_rel in image_files:
                serve_url = f'/extraction-image/{extraction_key}/{img_rel}'
                preview_content = preview_content.replace(img_rel, serve_url)

        return jsonify({
            'success': True,
            'content': preview_content,
            'raw_content': content,
            'download_key': extraction_key,
            'format': out_format,
            'filename': dl_filename,
            'image_count': len(image_files),
            'images': image_files,
        })
    except Exception as e:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        return jsonify({'error': f'Extraction failed: {str(e)}'}), 500


@app.route('/extraction-image/<key>/<path:img_path>')
def serve_extraction_image(key, img_path):
    """Serve an extracted image from the temp output directory."""
    entry = _extraction_store.get(key)
    if not entry:
        return 'Not found', 404
    safe_path = os.path.normpath(img_path)
    if safe_path.startswith('..') or os.path.isabs(safe_path):
        return 'Forbidden', 403
    full_path = os.path.join(entry['out_dir'], safe_path)
    if not os.path.isfile(full_path):
        return 'Not found', 404
    directory = os.path.dirname(full_path)
    filename = os.path.basename(full_path)
    return send_from_directory(directory, filename)


@app.route('/download-extraction-zip/<key>')
def download_extraction_zip(key):
    """Download all extraction output (main file + images) as a ZIP."""
    entry = _extraction_store.get(key)
    if not entry:
        return 'Not found', 404

    zip_buf = io.BytesIO()
    out_dir = entry['out_dir']
    with zipfile.ZipFile(zip_buf, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(out_dir):
            for fname in files:
                abs_path = os.path.join(root, fname)
                arc_name = os.path.relpath(abs_path, out_dir)
                zf.write(abs_path, arc_name)

    zip_buf.seek(0)
    stem = os.path.splitext(entry['filename'])[0]
    return send_file(zip_buf, mimetype='application/zip',
                     as_attachment=True, download_name=f'{stem}_extraction.zip')


@app.route('/cleanup-extraction/<key>', methods=['POST'])
def cleanup_extraction(key):
    """Clean up temp files for a completed extraction."""
    _cleanup_extraction(key)
    return jsonify({'ok': True})


# ── PEACE Sheet Generator ────────────────────────────────────────────────────

PS_MAIN_SECTIONS = ['Homework', 'Classwork', 'Class Test', 'Project']
PS_PROG_SECTIONS = ['MCQ', 'Essay', 'Mixed', 'Oral/Practical']


def _ps_build_slots():
    slots = []
    for sec in PS_MAIN_SECTIONS:
        for i in range(1, 11):
            slots.append({'section': sec, 'slot': str(i), 'is_total': False,
                          'key': f"{sec.lower().replace(' ', '')}_{i}"})
        slots.append({'section': sec, 'slot': 'Total', 'is_total': True,
                      'key': f"{sec.lower().replace(' ', '')}_total"})
    for sec in PS_PROG_SECTIONS:
        for i in range(1, 3):
            slots.append({'section': sec, 'slot': str(i), 'is_total': False,
                          'key': f"{sec.lower().replace('/', '')}_{i}"})
    return slots


PS_SLOTS = _ps_build_slots()
PS_TOTAL_COLS = 4 + len(PS_SLOTS)   # 56


def _ps_build_header_row():
    headers = ["S/N", "Student ID", "Surname", "First & Other Names"]
    for s in PS_SLOTS:
        if s['is_total']:
            headers.append(f"{s['section']} Total")
        else:
            headers.append(f"{s['section']} {s['slot']}")
    return headers


PS_HEADER_ROW = _ps_build_header_row()


def _ps_parse_assessment_column(col_name):
    """Parse "T.W.L.YY/YY - Type Name" column headers."""
    if ' - ' not in str(col_name):
        return None
    code, _ = str(col_name).split(' - ', 1)
    parts = code.split('.')
    if len(parts) < 2:
        return None
    try:
        term = int(parts[0])
        week = int(parts[1])
    except (ValueError, IndexError):
        return None
    year_raw = parts[3].strip() if len(parts) >= 4 else ''
    year_code = ''
    if '/' in year_raw:
        a, b = year_raw.split('/', 1)
        a, b = a.strip(), b.strip()
        year_code = f"20{a}_{b}" if len(a) == 2 else f"{a}_{b}"
    return {'term': term, 'week': week, 'code': code, 'year_code': year_code}


@app.route('/peace-sheet', methods=['GET', 'POST'])
def peace_upload():
    if request.method == 'POST':
        if 'csv_file' not in request.files:
            flash('No file part in the request.', 'error')
            return redirect(url_for('peace_upload'))

        file = request.files['csv_file']
        if file.filename == '':
            flash('No file selected.', 'error')
            return redirect(url_for('peace_upload'))

        if not file.filename.lower().endswith('.csv'):
            flash('Only .csv files are accepted.', 'error')
            return redirect(url_for('peace_upload'))

        try:
            raw = pd.read_csv(io.BytesIO(file.read()), header=None, dtype=str)
        except Exception as e:
            flash(f'Could not read CSV: {e}', 'error')
            return redirect(url_for('peace_upload'))

        if len(raw) < 4:
            flash('CSV must have at least 4 rows (headers, dates, points, 1+ students).', 'error')
            return redirect(url_for('peace_upload'))

        headers    = raw.iloc[0].tolist()
        points_row = raw.iloc[2].tolist()

        student_df = raw.iloc[3:].copy()
        # Deduplicate column names (e.g. when a Google Classroom export has two
        # columns with the same heading) so to_json(orient='records') doesn't fail.
        seen: dict[str, int] = {}
        unique_headers = []
        for h in headers:
            key = str(h) if pd.notna(h) else ''
            if key in seen:
                seen[key] += 1
                unique_headers.append(f"{key}.{seen[key]}")
            else:
                seen[key] = 0
                unique_headers.append(key)
        student_df.columns = unique_headers
        # Keep headers in sync with the deduplicated names for downstream logic.
        headers = unique_headers
        student_df = student_df.reset_index(drop=True)

        required = ['Surname', 'First Name', 'Email Address']
        for col in required:
            if col not in headers:
                flash(f'Missing required column: "{col}"', 'error')
                return redirect(url_for('peace_upload'))

        base_cols = required
        assessment_cols = [c for c in headers if c not in base_cols and pd.notna(c) and str(c).strip()]

        assessments_list = []
        for col in assessment_cols:
            meta = _ps_parse_assessment_column(col)
            if meta is None:
                continue
            col_idx = headers.index(col)
            pts_raw = points_row[col_idx] if col_idx < len(points_row) else ''
            try:
                pts = float(pts_raw) if pts_raw and str(pts_raw).strip() not in ('', 'nan') else 0.0
            except (ValueError, TypeError):
                pts = 0.0
            assessments_list.append({
                'id':              str(uuid.uuid4()),
                'display':         col,
                'original_header': col,
                'points':          pts,
                'week':            meta['week'],
                'term':            meta['term'],
                'year_code':       meta.get('year_code', ''),
                'input_col':       col,
            })

        if not assessments_list:
            flash('No valid assessment columns found (expected format: "T.W.L.YY/YY - Type Name").', 'error')
            return redirect(url_for('peace_upload'))

        ty_counts = Counter(
            (a['term'], a['year_code']) for a in assessments_list if a.get('year_code')
        )
        if ty_counts:
            dominant_term, dominant_year = ty_counts.most_common(1)[0][0]
        else:
            dominant_term = assessments_list[0]['term'] if assessments_list else 1
            dominant_year = ''

        session['ps_assessments']     = assessments_list
        session['ps_student_df_json'] = student_df.to_json(orient='records')
        session['ps_student_count']   = len(student_df)
        session['ps_detected_term']   = dominant_term
        session['ps_detected_year']   = dominant_year

        return redirect(url_for('peace_mapping'))

    return render_template('peace_upload.html')


@app.route('/peace-sheet/mapping')
def peace_mapping():
    if not session.get('ps_student_df_json'):
        flash('Please upload a file first.', 'error')
        return redirect(url_for('peace_upload'))

    assessments   = session.get('ps_assessments', [])
    student_count = session.get('ps_student_count', 0)
    detected_term = session.get('ps_detected_term')
    detected_year = session.get('ps_detected_year', '')

    term_years = sorted({
        (a['term'], a.get('year_code', ''))
        for a in assessments if a.get('year_code')
    })
    all_years = sorted({ty[1] for ty in term_years if ty[1]})
    all_terms = sorted({ty[0] for ty in term_years})

    return render_template(
        'peace_mapping.html',
        assessments=assessments,
        student_count=student_count,
        main_sections=PS_MAIN_SECTIONS,
        prog_sections=PS_PROG_SECTIONS,
        detected_term=detected_term,
        detected_year=detected_year,
        term_years=term_years,
        all_years=all_years,
        all_terms=all_terms,
    )


@app.route('/peace-sheet/preview', methods=['POST'])
def peace_preview():
    if not session.get('ps_student_df_json'):
        flash('Session expired. Please upload again.', 'error')
        return redirect(url_for('peace_upload'))

    meta = {
        'class_name':    request.form.get('class_name', '').strip(),
        'academic_year': request.form.get('academic_year', '').strip(),
        'term':          request.form.get('term', '').strip(),
        'subject':       request.form.get('subject', '').strip(),
        'class_size':    session.get('ps_student_count', 0),
    }

    mapping_dict = {}
    for key, value in request.form.items():
        if key.startswith('map_') and value:
            mapping_dict[key[4:]] = value

    session['ps_mapping']  = mapping_dict
    session['ps_metadata'] = meta

    errors = []
    for field in ('class_name', 'academic_year', 'term', 'subject'):
        if not meta[field]:
            errors.append(f'"{field.replace("_", " ").title()}" is required.')
    if not mapping_dict:
        errors.append('Please map at least one assessment.')
    if errors:
        for e in errors:
            flash(e, 'error')
        return redirect(url_for('peace_mapping'))

    assessments_list = session.get('ps_assessments', [])
    assess_map       = {a['id']: a for a in assessments_list}
    student_df       = pd.DataFrame(json.loads(session['ps_student_df_json']))
    student_df       = student_df.fillna('').astype(str)

    def _empty_row():
        return [''] * PS_TOTAL_COLS

    r0 = _empty_row()
    r0[0] = 'Class';          r0[1] = meta['class_name']
    r0[2] = 'Academic Year';  r0[3] = meta['academic_year']
    r0[4] = 'Term';           r0[5] = meta['term']
    r0[6] = 'Class Size';     r0[7] = str(meta['class_size'])
    r0[8] = 'Subject';        r0[9] = meta['subject']

    r_tag  = _empty_row(); r_tag[0]  = 'Assignment Tag'
    r_week = _empty_row(); r_week[0] = 'Week of Entry in ISMS'
    r_mark = _empty_row(); r_mark[0] = 'Total Mark for Assessment [Den]'

    for pos, slot in enumerate(PS_SLOTS):
        col_i = 4 + pos
        if slot['is_total']:
            r_tag[col_i] = 'Section Total'
            continue
        aid = mapping_dict.get(slot['key'])
        if aid and aid in assess_map:
            a = assess_map[aid]
            r_tag[col_i]  = a['original_header']
            r_week[col_i] = str(a['week'])
            r_mark[col_i] = str(a['points'])

    rows = [r0, r_tag, r_week, r_mark, PS_HEADER_ROW[:]]

    for s_idx, student in student_df.iterrows():
        row = _empty_row()
        row[0] = str(s_idx + 1)
        email = str(student.get('Email Address', ''))
        row[1] = email.split('@')[0] if '@' in email else email
        row[2] = str(student.get('Surname', ''))
        row[3] = str(student.get('First Name', ''))

        for pos, slot in enumerate(PS_SLOTS):
            col_i = 4 + pos
            if slot['is_total']:
                sec = slot['section']
                total = 0.0
                had_score = False
                for sub_slot in PS_SLOTS:
                    if sub_slot['section'] == sec and not sub_slot['is_total']:
                        sub_aid = mapping_dict.get(sub_slot['key'])
                        if sub_aid and sub_aid in assess_map:
                            orig = assess_map[sub_aid]['input_col']
                            val  = student.get(orig, '')
                            try:
                                total += float(val)
                                had_score = True
                            except (ValueError, TypeError):
                                pass
                row[col_i] = f"{total:.2f}" if had_score else ''
            else:
                aid = mapping_dict.get(slot['key'])
                if aid and aid in assess_map:
                    orig = assess_map[aid]['input_col']
                    val  = student.get(orig, '')
                    row[col_i] = '' if (val is None or str(val).strip() in ('', 'nan', 'None')) else str(val)

        rows.append(row)

    filename = (
        f"{meta['class_name']} PEACE SHEET "
        f"{meta['academic_year']} {meta['term']}.csv"
    )

    return render_template(
        'peace_preview.html',
        rows=rows,
        columns=PS_HEADER_ROW,
        filename=filename,
        meta=meta,
        mapping_dict=mapping_dict,
        student_count=session.get('ps_student_count', 0),
        preview_count=len(student_df),
    )


@app.route('/peace-sheet/download', methods=['POST'])
def peace_download():
    if not session.get('ps_student_df_json'):
        flash('Session expired. Please upload again.', 'error')
        return redirect(url_for('peace_upload'))

    mapping_dict     = session.get('ps_mapping', {})
    meta             = session.get('ps_metadata', {})
    assessments_list = session.get('ps_assessments', [])
    assess_map       = {a['id']: a for a in assessments_list}
    student_df       = pd.DataFrame(json.loads(session['ps_student_df_json']))
    student_df       = student_df.fillna('').astype(str)

    output = io.StringIO()
    writer = csv.writer(output)

    def _empty_row():
        return [''] * PS_TOTAL_COLS

    r0 = _empty_row()
    r0[0] = 'Class';          r0[1] = meta.get('class_name', '')
    r0[2] = 'Academic Year';  r0[3] = meta.get('academic_year', '')
    r0[4] = 'Term';           r0[5] = meta.get('term', '')
    r0[6] = 'Class Size';     r0[7] = str(meta.get('class_size', len(student_df)))
    r0[8] = 'Subject';        r0[9] = meta.get('subject', '')
    writer.writerow(r0)

    r_tag  = _empty_row(); r_tag[0]  = 'Assignment Tag'
    r_week = _empty_row(); r_week[0] = 'Week of Entry in ISMS'
    r_mark = _empty_row(); r_mark[0] = 'Total Mark for Assessment [Den]'

    for pos, slot in enumerate(PS_SLOTS):
        col_i = 4 + pos
        if slot['is_total']:
            r_tag[col_i] = 'Section Total'
            continue
        aid = mapping_dict.get(slot['key'])
        if aid and aid in assess_map:
            a = assess_map[aid]
            r_tag[col_i]  = a['original_header']
            r_week[col_i] = str(a['week'])
            r_mark[col_i] = str(a['points'])

    writer.writerow(r_tag)
    writer.writerow(r_week)
    writer.writerow(r_mark)
    writer.writerow(PS_HEADER_ROW)

    for s_idx, student in student_df.iterrows():
        row = _empty_row()
        row[0] = str(s_idx + 1)
        email = str(student.get('Email Address', ''))
        row[1] = email.split('@')[0] if '@' in email else email
        row[2] = str(student.get('Surname', ''))
        row[3] = str(student.get('First Name', ''))

        for pos, slot in enumerate(PS_SLOTS):
            col_i = 4 + pos
            if slot['is_total']:
                sec = slot['section']
                total = 0.0
                had_score = False
                for sub_slot in PS_SLOTS:
                    if sub_slot['section'] == sec and not sub_slot['is_total']:
                        sub_aid = mapping_dict.get(sub_slot['key'])
                        if sub_aid and sub_aid in assess_map:
                            orig = assess_map[sub_aid]['input_col']
                            val  = student.get(orig, '')
                            try:
                                total += float(val)
                                had_score = True
                            except (ValueError, TypeError):
                                pass
                row[col_i] = f"{total:.2f}" if had_score else ''
            else:
                aid = mapping_dict.get(slot['key'])
                if aid and aid in assess_map:
                    orig = assess_map[aid]['input_col']
                    val  = student.get(orig, '')
                    row[col_i] = '' if (val is None or str(val).strip() in ('', 'nan', 'None')) else str(val)

        writer.writerow(row)

    csv_bytes = output.getvalue().encode('utf-8-sig')

    # Clear only peacesheet session keys
    for key in ('ps_assessments', 'ps_student_df_json', 'ps_student_count',
                'ps_detected_term', 'ps_detected_year', 'ps_mapping', 'ps_metadata'):
        session.pop(key, None)

    filename = (
        f"{meta.get('class_name', 'CLASS')} PEACE SHEET "
        f"{meta.get('academic_year', '')} {meta.get('term', '')}.csv"
    )

    return send_file(
        io.BytesIO(csv_bytes),
        mimetype='text/csv',
        as_attachment=True,
        download_name=filename,
    )


if __name__ == '__main__':
    app.run(host='0.0.0.0', debug=False, port=5000)