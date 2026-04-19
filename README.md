# EduTools

A Flask-based web application providing five productivity tools for educators.

Built by **iRBbA Devs**.

---

## Modules

| Module | Route | Description |
|---|---|---|
| **Group Generator** | `/group-generator` | Upload a student CSV and generate gender-balanced groups. |
| **Student Picker** | `/student-picker` | Randomly select students from a loaded list. |
| **Assessment Generator** | `/assessment-generator` | Parse structured assessment CSVs and export per-week score sheets. |
| **PDF Extractor** | `/pdf-extractor` | Convert uploaded PDF files to Markdown, HTML, JSON, or plain text. |
| **PEACE Sheet Generator** | `/peace-mapping` | Build PEACE-format assessment sheets from uploaded score data. |

---

## Requirements

- Python 3.10 or higher
- Java 11 or higher *(required by the PDF Extractor module)*

---

## Installation

```bash
# 1. Clone the repository
git clone https://github.com/RaymondAffedzie/edutools.git
cd edutools

# 2. Create and activate a virtual environment
python -m venv .venv
source .venv/bin/activate      # macOS / Linux
.venv\Scripts\activate         # Windows

# 3. Install Python dependencies
pip install -r requirements.txt
```

---

## Running the App

```bash
# Development server (auto-reload enabled)
flask --app app run --debug

# Or directly
python app.py
```

The app will be available at `http://127.0.0.1:5000`.

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `edutools-dev-secret-2026` | Flask session secret key. **Change this in production.** |

> **Security note:** Always set a strong, random `SECRET_KEY` environment variable before deploying to production. Never use the default key in a live environment.

---

## CSV Format Reference

### Group Generator

The input CSV must have four columns in this order (no header required):

```
StudentID, Surname, FirstNames, Sex
```

- `Sex` must be `M` or `F`.

A template file is provided at `templates/Group Generation Template.csv`.

### Assessment Generator

Assessment column headers must follow the pattern:

```
TERM.WEEK.LESSON.YY/YY - AssessmentType Name
```

Example: `1.3.2.24/25 - Homework Q1`

---

## Project Structure

```
app.py                  # Main Flask application
requirements.txt        # Python dependencies
static/
  css/
    style.css           # Global stylesheet
templates/
  index.html            # Landing page
  group_generator.html  # Group Generator UI
  student_picker.html   # Student Picker UI
  assessment.html       # Assessment Generator UI
  pdf_extractor.html    # PDF Extractor UI
  peace_upload.html     # PEACE Sheet upload
  peace_mapping.html    # PEACE Sheet mapping UI
  peace_preview.html    # PEACE Sheet preview
flask_session/          # Server-side session storage (auto-created)
```

---

## Third-Party Licenses

This project uses the following open-source libraries. See [NOTICE](NOTICE) for full attribution details.

| Package | Version | License |
|---|---|---|
| Flask | ≥ 3.1.3 | BSD-3-Clause |
| Flask-Session | ≥ 0.8.0 | BSD-3-Clause |
| Werkzeug | ≥ 3.1 | BSD-3-Clause |
| Jinja2 | ≥ 3.1 | BSD-3-Clause |
| MarkupSafe | ≥ 3.0 | BSD-3-Clause |
| itsdangerous | ≥ 2.2 | BSD-3-Clause |
| click | ≥ 8.3 | BSD-3-Clause |
| pandas | ≥ 2.0 | BSD-3-Clause |
| opendataloader-pdf | ≥ 2.2.1 | Apache-2.0 |

The `opendataloader-pdf` package is licensed under the Apache License 2.0. A copy of that license and the required attribution notice are included in the [NOTICE](NOTICE) file as required by the Apache License, Section 4(d).

---

## License

This project is licensed under the **MIT License**. See [LICENSE](LICENSE) for the full text.
