import streamlit as st
import pandas as pd
import requests
from io import BytesIO
from PyPDF2 import PdfMerger
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.lib.utils import ImageReader
from datetime import datetime
from pathlib import Path
import tempfile, os
from reportlab.pdfbase.ttfonts import TTFont
import base64
from PIL import Image

# --- Initial Setup ---
CACHE_DIR = Path(__file__).parent / "pdf_cache"
CACHE_DIR.mkdir(exist_ok=True)
APP_DIR = Path(__file__).parent
LOGO_FILENAME = "Jomar Valve Logo Red.png"
default_logo_path = str(APP_DIR / LOGO_FILENAME)

# --- Font Registration ---
FONT_PATH = Path(__file__).parent / "Proxima Nova Font.ttf"
if FONT_PATH.exists():
    try:
        pdfmetrics.registerFont(TTFont("ProximaNova", str(FONT_PATH)))
        FONT_TITLE = FONT_TEXT = "ProximaNova"
    except Exception:
        FONT_TITLE = FONT_TEXT = "Helvetica"
else:
    FONT_TITLE = FONT_TEXT = "Helvetica"

# --- Drag & Drop Fallback ---
try:
    from streamlit_sortables import sort_items
except ImportError:
    sort_items = lambda items, **kwargs: items 

# --- Networking Helpers ---
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry

def create_session_with_retries(retries=3, backoff_factor=2):
    session = requests.Session()
    retry = Retry(total=retries, read=retries, connect=retries, backoff_factor=backoff_factor, status_forcelist=[500, 502, 503, 504])
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter); session.mount("http://", adapter)
    return session

def fetch_pdf_cached(url: str):
    import hashlib
    filename = hashlib.md5(url.encode()).hexdigest() + ".pdf"
    file_path = CACHE_DIR / filename
    if file_path.exists():
        with open(file_path, "rb") as f: return f.read()
    session = create_session_with_retries(retries=4, backoff_factor=1.5)
    resp = session.get(url, timeout=25)
    resp.raise_for_status()
    pdf_bytes = resp.content
    with open(file_path, "wb") as f: f.write(pdf_bytes)
    return pdf_bytes

# --- Cover Page Helpers ---
def hex_to_rgb01(hex_color: str):
    h = hex_color.strip().lstrip("#")
    return int(h[0:2], 16)/255.0, int(h[2:4], 16)/255.0, int(h[4:6], 16)/255.0

def fit_multiline_text(lines, font_name, bar_width, bar_height, side_pad=48, v_pad=18, max_pt=30, min_pt=14, leading_factor=1.12):
    safe_w, safe_h = max(bar_width - 2*side_pad, 1), max(bar_height - 2*v_pad, 1)
    caps = [safe_w / pdfmetrics.stringWidth(txt, font_name, 1.0) for txt in lines if txt]
    width_cap = min(caps) if caps else max_pt
    height_cap = (safe_h / ((len(lines) - 1) * leading_factor)) if len(lines) > 1 else max_pt
    size = max(min(width_cap, height_cap, max_pt), min_pt)
    return [size] * len(lines), size * leading_factor

def draw_centered_stack(c, x_center, y_center, lines, sizes, font_name, color_rgb, leading=26):
    if not lines: return
    asc0 = (pdfmetrics.getAscent(font_name) / 1000.0) * sizes[0]
    block_h = asc0 + (leading * (len(lines) - 1))
    first_baseline_y = y_center + (block_h / 2.0) - asc0
    c.setFillColorRGB(*color_rgb)
    for i, (txt, sz) in enumerate(zip(lines, sizes)):
        c.setFont(font_name, sz)
        c.drawCentredString(x_center, first_baseline_y - i * leading, txt)

def make_cover_pdf(outfile, logo_path, project_name, project_location, party_label, party_name, date_prepared, bid_date, bid_date_tbc=False, bid_date_na=False):
    c = canvas.Canvas(outfile, pagesize=letter)
    width, height = letter
    c.setLineWidth(1); c.setStrokeColorRGB(0.85, 0.85, 0.85)
    c.rect(36, 36, width - 72, height - 72, stroke=1, fill=0)
    bar_h, bar_y = 140, (height / 2.0) - 70
    c.setFillColorRGB(188/255, 20/255, 27/255) # Jomar Red
    c.rect(0, bar_y, width, bar_h, stroke=0, fill=1)
    if logo_path and os.path.exists(logo_path):
        img = ImageReader(logo_path)
        iw, ih = img.getSize()
        scale = min(300 / iw, 1.0)
        c.drawImage(logo_path, (width - iw*scale)/2, ((height + bar_y + bar_h)/2) - (ih*scale/2), width=iw*scale, height=ih*scale, preserveAspectRatio=True, mask='auto')
    title_lines = [(project_name or "TO BE CONFIRMED").upper(), (project_location or "TO BE CONFIRMED").upper(), "SUBMITTAL PACKAGE"]
    sizes, dyn_leading = fit_multiline_text(title_lines, FONT_TITLE, width, bar_h)
    draw_centered_stack(c, width / 2.0, bar_y + bar_h / 2.0, title_lines, sizes, FONT_TITLE, (1, 1, 1), dyn_leading)
    lines_bottom = [f"{(party_label or 'Recipient').upper()}: {(party_name or 'To Be Confirmed').upper()}", f"DATE PREPARED: {str(date_prepared).upper()}"]
    if not bid_date_na: lines_bottom.append(f"BID DATE: {str(bid_date or 'To Be Confirmed').upper()}")
    draw_centered_stack(c, width/2, 140, lines_bottom, [12]*len(lines_bottom), FONT_TEXT, (0,0,0), 18)
    c.showPage(); c.save()

# =====================================================
# App UI & Styling
# =====================================================
st.set_page_config(page_title="Jomar Valve Submittal Creator", layout="wide")

st.markdown("""
<style>
    /* Jomar Red Theme */
    :root { --primary-color: #BC141B; }
    section[data-testid="stSidebar"] { background-color: #f9f9f9; }
    .stButton>button { background-color: #BC141B !important; color: white !important; }
    
    /* Sortables Styling */
    div[data-testid="sortable-item"] { 
        background-color: #BC141B !important; 
        color: white !important; 
        border-radius: 4px !important; 
        padding: 8px !important;
        font-weight: 600 !important;
    }

    /* Small Sidebar Delete Buttons */
    .sidebar-del-btn button {
        height: 24px !important;
        width: 24px !important;
        padding: 0 !important;
        font-size: 12px !important;
        line-height: 24px !important;
        border-radius: 50% !important;
        background-color: #eee !important;
        color: #BC141B !important;
        border: 1px solid #ddd !important;
    }
    .sidebar-del-btn button:hover {
        background-color: #BC141B !important;
        color: white !important;
    }
</style>
""", unsafe_allow_html=True)

# --- Session State ---
st.session_state.setdefault("queue", [])

# --- Layout Header ---
c1, c2 = st.columns([3, 1], vertical_alignment="center")
with c1:
    st.markdown('<h1 style="margin-top:-6rem;">JOMAR VALVE SUBMITTAL PACKAGE CREATOR</h1>', unsafe_allow_html=True)
with c2:
    if Path(default_logo_path).exists():
        st.image(default_logo_path, width=200)

# ---------------- Sidebar: Queue & Management ----------------
with st.sidebar:
    st.markdown("### Selected Spec Sheets")

    def _get_lbl(obj):
        if isinstance(obj, dict): return obj.get("Model", "Unknown")
        return os.path.splitext(getattr(obj, "name", "File"))[0]

    if not st.session_state.queue:
        st.info("No items selected.")
    else:
        # 1. DRAG AND DROP (The Red Boxes)
        st.markdown("**Click & Drag to Reorder:**")
        raw_labels = [_get_lbl(x) for x in st.session_state.queue]
        # We add a hidden index to handle duplicate model names
        indexed_labels = [f"{lbl} ##{i}" for i, lbl in enumerate(raw_labels)]
        
        sorted_indexed = sort_items(indexed_labels, direction="vertical", key=f"sort_{len(st.session_state.queue)}")
        
        # Check for reorder
        if sorted_indexed != indexed_labels:
            new_queue = []
            for lab in sorted_indexed:
                idx = int(lab.split("##")[-1])
                new_queue.append(st.session_state.queue[idx])
            st.session_state.queue = new_queue
            st.rerun()

        # 2. DELETE SECTION (Right below the red boxes)
        st.markdown("---")
        st.markdown("**Remove Items:**")
        for i, item in enumerate(list(st.session_state.queue)):
            col_txt, col_btn = st.columns([5, 1])
            col_txt.markdown(f"<p style='font-size:0.85rem; margin-top:4px;'>{_get_lbl(item)}</p>", unsafe_allow_html=True)
            with col_btn:
                st.markdown('<div class="sidebar-del-btn">', unsafe_allow_html=True)
                if st.button("X", key=f"del_{i}"):
                    st.session_state.queue.pop(i)
                    if "generated_pdf" in st.session_state: del st.session_state["generated_pdf"]
                    st.rerun()
                st.markdown('</div>', unsafe_allow_html=True)

    st.markdown("---")
    if st.button("Clear All", use_container_width=True):
        st.session_state.queue = []; st.rerun()

    if st.session_state.queue:
        if st.button("Create Package", type="primary", use_container_width=True):
            with st.spinner("Building PDF..."):
                tmp_c = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                make_cover_pdf(tmp_c.name, default_logo_path, st.session_state.get("proj_n"), st.session_state.get("proj_l"), 
                               st.session_state.get("role"), st.session_state.get("comp"), st.session_state.get("d_prep"), 
                               st.session_state.get("bd_date"), False, st.session_state.get("bd_na"))
                merger = PdfMerger()
                merger.append(tmp_c.name)
                for f in st.session_state.queue:
                    f.seek(0); merger.append(f)
                out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                merger.write(out_tmp.name); merger.close()
                with open(out_tmp.name, "rb") as f: st.session_state["generated_pdf"] = f.read()
                st.toast("✅ Created!")

        if "generated_pdf" in st.session_state:
            st.download_button("Download PDF", data=st.session_state["generated_pdf"], file_name="Jomar_Submittal.pdf", use_container_width=True)

# ---------------- Main Page: Inputs & Catalog ----------------
st.subheader("COVER PAGE DETAILS")
# (Simple input mapping for brevity)
st.session_state["role"] = st.radio("Recipient Role", ["Contractor", "Engineer", "Distributor", "Utility"], horizontal=True)
st.session_state["comp"] = st.text_input("Company Name")
st.session_state["proj_n"] = st.text_input("Project Name")
st.session_state["proj_l"] = st.text_input("Project Location")
st.session_state["d_prep"] = st.date_input("Date Prepared", datetime.now())
st.session_state["bd_na"] = st.checkbox("No Bid Date")

st.markdown("---")
st.subheader("UPLOAD PDFS")
up = st.file_uploader("Upload additional spec sheets", type="pdf", accept_multiple_files=True)
if up:
    for f in up:
        if f.name not in [getattr(x, 'name', '') for x in st.session_state.queue]:
            st.session_state.queue.append(f)
    st.rerun()

st.markdown("---")
st.subheader("SPEC SHEET LIBRARY")
try:
    df = pd.read_excel("spec_links_images.xlsx").dropna(subset=["Model", "URL"])
    cat = st.selectbox("Category", sorted(df["Category"].unique()))
    filtered = df[df["Category"] == cat]
    
    for _, row in filtered.iterrows():
        c1, c2 = st.columns([1, 4])
        with c1: st.image(row["Image"], width=100) if pd.notnull(row["Image"]) else st.write("No Image")
        with c2:
            st.markdown(f"**{row['Model']}**\n\n{row['Description']}")
            if st.button(f"Add {row['Model']}", key=f"add_{row['Model']}"):
                target = f"{row['Model']}.pdf"
                if target not in [getattr(x, 'name', '') for x in st.session_state.queue]:
                    b = fetch_pdf_cached(row["URL"])
                    f = BytesIO(b); f.name = target
                    st.session_state.queue.append(f); st.rerun()
except Exception as e:
    st.info("Please ensure 'spec_links_images.xlsx' is present.")
