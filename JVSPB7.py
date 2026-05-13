import streamlit as st
import pandas as pd
import requests
from io import BytesIO
from PyPDF2 import PdfMerger
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from datetime import datetime
from pathlib import Path
import tempfile, os
from reportlab.pdfbase.ttfonts import TTFont
import base64
from PIL import Image

CACHE_DIR = Path(__file__).parent / "pdf_cache"
CACHE_DIR.mkdir(exist_ok=True)

# --- Register Proxima Nova Font (or fallback to Helvetica) ---
FONT_PATH = Path(__file__).parent / "Proxima Nova Font.ttf"

if FONT_PATH.exists():
    try:
        pdfmetrics.registerFont(TTFont("ProximaNova", str(FONT_PATH)))
        FONT_TITLE = FONT_TEXT = "ProximaNova"
    except Exception:
        FONT_TITLE = FONT_TEXT = "Helvetica"
else:
    FONT_TITLE = FONT_TEXT = "Helvetica"

# ---- Drag & Drop (Sortables) ----
try:
    from streamlit_sortables import sort_items
except ImportError:
    st.warning("⚠️ streamlit-sortables is not installed. Drag & drop ordering will be disabled.")
    sort_items = lambda items, **kwargs: items  

# ---- Networking Session ----
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

def create_session_with_retries(retries=3, backoff_factor=2):
    session = requests.Session()
    retry = Retry(
        total=retries,
        read=retries,
        connect=retries,
        backoff_factor=backoff_factor,
        status_forcelist=[500, 502, 503, 504],
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    return session

def fetch_pdf_cached(url: str):
    import hashlib
    filename = hashlib.md5(url.encode()).hexdigest() + ".pdf"
    file_path = CACHE_DIR / filename
    if file_path.exists():
        with open(file_path, "rb") as f:
            return f.read()
    session = create_session_with_retries(retries=4, backoff_factor=1.5)
    resp = session.get(url, timeout=25)
    resp.raise_for_status()
    pdf_bytes = resp.content
    with open(file_path, "wb") as f:
        f.write(pdf_bytes)
    return pdf_bytes

# =====================================================
# PDF Generation Helpers
# =====================================================
def hex_to_rgb01(hex_color: str):
    h = hex_color.strip().lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return r, g, b

def fit_multiline_text(lines, font_name, bar_width, bar_height, side_pad=48, v_pad=18, max_pt=36, min_pt=14, leading_factor=1.12):
    safe_w = max(bar_width - 2*side_pad, 1)
    safe_h = max(bar_height - 2*v_pad, 1)
    caps = []
    for txt in lines:
        if not txt: continue
        base_w_at_1pt = pdfmetrics.stringWidth(txt, font_name, 1.0)
        if base_w_at_1pt > 0:
            caps.append(safe_w / base_w_at_1pt)
    width_cap = min(caps) if caps else max_pt
    n = len(lines)
    height_cap = (safe_h / ((n - 1) * leading_factor)) if n > 1 else max_pt
    size = max(min(width_cap, height_cap, max_pt), min_pt)
    leading = size * leading_factor
    return [size] * n, leading

def draw_centered_stack(c, x_center, y_center, lines, sizes, font_name, color_rgb, leading=26):
    if not lines: return
    asc_u = pdfmetrics.getAscent(font_name) / 1000.0
    des_u = abs(pdfmetrics.getDescent(font_name) / 1000.0)
    asc0 = asc_u * sizes[0]
    des_last = des_u * sizes[-1]
    interline = leading * (len(lines) - 1)
    block_h = asc0 + interline + des_last
    first_baseline_y = y_center + (block_h / 2.0) - asc0

    c.setFillColorRGB(*color_rgb)
    for i, (txt, sz) in enumerate(zip(lines, sizes)):
        y = first_baseline_y - i * leading
        c.setFont(font_name, sz)
        c.drawCentredString(x_center, y, txt)

def format_mdY(d, blank="To Be Confirmed"):
    if not d: return blank
    return f"{d.month}/{d.day}/{d.year}"

def make_cover_pdf(outfile, project_name, project_location, party_label, party_name, date_prepared, bid_date, bid_date_tbc=False, bid_date_na=False):
    c = canvas.Canvas(outfile, pagesize=letter)
    width, height = letter

    # Border
    border_inset = 36
    c.setLineWidth(1)
    c.setStrokeColorRGB(*hex_to_rgb01("#D9D9D9"))
    c.rect(border_inset, border_inset, width - 2*border_inset, height - 2*border_inset, stroke=1, fill=0)

    # Red bar
    BAR_COLOR = "#BC141B"
    bar_rgb = hex_to_rgb01(BAR_COLOR)
    bar_height = 140
    bar_y = (height / 2.0) - (bar_height / 2.0)
    c.setFillColorRGB(*bar_rgb)
    c.rect(0, bar_y, width, bar_height, stroke=0, fill=1)

    # Title inside bar
    title_lines = [
        (project_name or "TO BE CONFIRMED").upper(),
        (project_location or "TO BE CONFIRMED").upper(),
        "SUBMITTAL PACKAGE",
    ]
    sizes, dyn_leading = fit_multiline_text(title_lines, FONT_TITLE, width, bar_height)
    draw_centered_stack(c, width / 2.0, bar_y + bar_height / 2.0, title_lines, sizes, FONT_TITLE, (1, 1, 1), dyn_leading)

    # Bottom labels
    role_label = (party_label or "Recipient").upper()
    company_txt = (party_name or "To Be Confirmed").upper()
    date_prep_txt = format_mdY(date_prepared).upper()
    lines_bottom = [f"{role_label}: {company_txt}", f"DATE PREPARED: {date_prep_txt}"]

    if not bid_date_na:
        bid_txt = "TO BE CONFIRMED" if (bid_date_tbc or not bid_date) else format_mdY(bid_date).upper()
        lines_bottom.append(f"BID DATE: {bid_txt}")

    draw_centered_stack(c, width / 2.0, 140, lines_bottom, [12] * len(lines_bottom), FONT_TEXT, (0, 0, 0), 18)
    c.showPage()
    c.save()

# =====================================================
# UI Logic
# =====================================================
st.set_page_config(page_title="Jomar Valve Submittal Creator", layout="wide")

# Header Section (Logo removed)
st.markdown("""
    <div style="text-align: center; margin-top: -2rem;">
        <h1 style="font-size: 2.4rem;">JOMAR VALVE SUBMITTAL PACKAGE CREATOR</h1>
        <p style="font-size: 1.1rem;">Upload PDFs, select from the catalog, and generate a custom submittal package.</p>
    </div>
    <hr>
""", unsafe_allow_html=True)

# --- State Management ---
if "queue" not in st.session_state: st.session_state.queue = []
if "uploads" not in st.session_state: st.session_state.uploads = []

# --- Cover Page Inputs ---
st.subheader("COVER PAGE DETAILS")
col_a, col_b = st.columns(2)
with col_a:
    party_name = st.text_input("Company Name")
    project_name = st.text_input("Project Name")
with col_b:
    project_location = st.text_input("Project Location")
    date_prepared = st.date_input("Date Prepared", value=datetime.now())

st.markdown("**Recipient Role:**")
role_cols = st.columns(4)
roles = ["Contractor", "Engineer", "Distributor", "Utility"]
selected_role = None
for i, r in enumerate(roles):
    if role_cols[i].checkbox(r, key=f"role_{r}"):
        selected_role = r

# Bid Date Logic
st.markdown("**Bid Date Options:**")
c1, c2, c3 = st.columns([2, 1, 1])
bd_tbc = c2.checkbox("To Be Confirmed")
bd_na = c3.checkbox("Not Applicable")
bd_date = c1.date_input("Select Bid Date", disabled=(bd_tbc or bd_na))

# --- Sidebar: Queue Management ---
with st.sidebar:
    st.title("Selected Sheets")
    if not st.session_state.queue:
        st.info("Queue is empty.")
    else:
        labels = [getattr(f, "name", "Unnamed File") for f in st.session_state.queue]
        sorted_labels = sort_items(labels, key="sidebar_sort")
        
        # Sync queue with sorted labels
        new_queue = []
        for label in sorted_labels:
            for item in st.session_state.queue:
                if getattr(item, "name", "") == label:
                    new_queue.append(item)
                    break
        st.session_state.queue = new_queue

    if st.button("Clear Queue"):
        st.session_state.queue = []
        st.rerun()

    if st.session_state.queue:
        if st.button("Generate Final PDF", type="primary"):
            with st.spinner("Merging files..."):
                cover_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                make_cover_pdf(cover_tmp.name, project_name, project_location, selected_role, party_name, date_prepared, bd_date, bd_tbc, bd_na)
                
                merger = PdfMerger()
                merger.append(cover_tmp.name)
                for f in st.session_state.queue:
                    f.seek(0)
                    merger.append(f)
                
                output = BytesIO()
                merger.write(output)
                st.session_state.final_pdf = output.getvalue()
                st.success("Package Ready!")

        if "final_pdf" in st.session_state:
            st.download_button("Download Submittal", data=st.session_state.final_pdf, file_name="Submittal_Package.pdf", mime="application/pdf")

# --- File Uploader ---
st.subheader("ADD FILES")
uploaded_files = st.file_uploader("Upload local PDFs", type="pdf", accept_multiple_files=True)
if uploaded_files:
    for f in uploaded_files:
        if f.name not in [getattr(q, "name", "") for q in st.session_state.queue]:
            st.session_state.queue.append(f)
    st.rerun()

# --- Catalog Placeholder ---
st.markdown("---")
st.subheader("SPEC SHEET LIBRARY")
st.info("Excel Catalog Loading... (Ensure 'spec_links_images.xlsx' is in the directory)")
