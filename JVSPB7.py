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

# ---- Networking Helpers ----
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
# Helpers for cover page
# =====================================================
def hex_to_rgb01(hex_color: str):
    h = hex_color.strip().lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return r, g, b

def fit_multiline_text(lines, font_name, bar_width, bar_height,
                       side_pad=48, v_pad=18,
                       max_pt=36, min_pt=14,
                       leading_factor=1.12, letter_spacing=0.0):
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

def draw_logo_centered_between_page_top_and_bar_top(c, logo_path, max_width, page_width, page_height, bar_top_y):
    img = ImageReader(logo_path)
    iw, ih = img.getSize()
    scale = min(max_width / iw, 1.0)
    w = iw * scale
    h = ih * scale
    x = (page_width - w) / 2.0
    desired_center_y = (page_height + bar_top_y) / 2.0
    y = desired_center_y - (h / 2.0)
    y = min(y, page_height - h - 24)
    c.drawImage(logo_path, x, y, width=w, height=h, preserveAspectRatio=True, mask='auto')

def draw_centered_stack(c, x_center, y_center, lines, sizes, font_name, color_rgb, leading=26, letter_spacing=0.0, optical_adjust=0.0):
    if not lines: return
    asc_u = pdfmetrics.getAscent(font_name) / 1000.0
    des_u = abs(pdfmetrics.getDescent(font_name) / 1000.0)
    asc0 = asc_u * sizes[0]
    des_last = des_u * sizes[-1]
    interline = leading * (len(lines) - 1)
    block_h = asc0 + interline + des_last
    first_baseline_y = y_center + (block_h / 2.0) - asc0 + optical_adjust

    c.setFillColorRGB(*color_rgb)
    for i, (txt, sz) in enumerate(zip(lines, sizes)):
        y = first_baseline_y - i * leading
        c.setFont(font_name, sz)
        c.drawCentredString(x_center, y, txt)

def format_mdY(d, blank="To Be Confirmed"):
    if not d: return blank
    return f"{d.month}/{d.day}/{d.year}"

def role_checkbox_group(key_prefix="role"):
    roles = ["Contractor", "Engineer", "Distributor", "Utility"]
    keys = [f"{key_prefix}_{r.lower()}" for r in roles]
    def _set_only(this_key):
        for k in keys:
            if k != this_key: st.session_state[k] = False
    cols = st.columns(len(roles))
    for r, k, col in zip(roles, keys, cols):
        with col: st.checkbox(r, key=k, on_change=_set_only, args=(k,))
    for r, k in zip(roles, keys):
        if st.session_state.get(k): return r
    return None

def bid_date_picker_with_flags(label: str, key: str):
    tbc_key, na_key = f"{key}_tbc", f"{key}_na"
    def _on_tbc_change():
        if st.session_state.get(tbc_key, False): st.session_state[na_key] = False
    def _on_na_change():
        if st.session_state.get(na_key, False): st.session_state[tbc_key] = False
    disabled = st.session_state.get(tbc_key, False) or st.session_state.get(na_key, False)
    date_val = st.date_input(label, key=f"{key}_date", disabled=disabled)
    cols = st.columns(2)
    with cols[0]: st.checkbox("Bid Date To Be Confirmed", key=tbc_key, on_change=_on_tbc_change)
    with cols[1]: st.checkbox("Bid Date Not Applicable",  key=na_key,  on_change=_on_na_change)
    if st.session_state.get(tbc_key, False) or st.session_state.get(na_key, False):
        date_val = None
    return date_val, st.session_state.get(tbc_key, False), st.session_state.get(na_key, False)

def make_cover_pdf(outfile, logo_path, project_name, project_location, party_label, party_name, date_prepared, bid_date, bid_date_tbc=False, bid_date_na=False):
    c = canvas.Canvas(outfile, pagesize=letter)
    width, height = letter
    border_inset = 36
    c.setLineWidth(1)
    c.setStrokeColorRGB(*hex_to_rgb01("#D9D9D9"))
    c.rect(border_inset, border_inset, width - 2*border_inset, height - 2*border_inset, stroke=1, fill=0)

    BAR_COLOR = "#BC141B"
    bar_rgb = hex_to_rgb01(BAR_COLOR)
    bar_height = 140
    bar_y = (height / 2.0) - (bar_height / 2.0)
    bar_top_y = bar_y + bar_height
    c.setFillColorRGB(*bar_rgb)
    c.rect(0, bar_y, width, bar_height, stroke=0, fill=1)

    if logo_path and os.path.exists(logo_path):
        draw_logo_centered_between_page_top_and_bar_top(c, logo_path, 300, width, height, bar_top_y)

    title_lines = [(project_name or "TO BE CONFIRMED").upper(), (project_location or "TO BE CONFIRMED").upper(), "SUBMITTAL PACKAGE"]
    sizes, dyn_leading = fit_multiline_text(title_lines, FONT_TITLE, width, bar_height)
    draw_centered_stack(c, width / 2.0, bar_y + bar_height / 2.0, title_lines, sizes, FONT_TITLE, (1, 1, 1), dyn_leading)

    bottom_block_y = 140
    role_label = (party_label or "Recipient").upper()
    company_txt = (party_name or "To Be Confirmed").upper()
    lines_bottom = [f"{role_label}: {company_txt}", f"DATE PREPARED: {format_mdY(date_prepared).upper()}"]
    if not bid_date_na:
        lines_bottom.append(f"BID DATE: {format_mdY(bid_date).upper()}")

    draw_centered_stack(c, width / 2.0, bottom_block_y, lines_bottom, [12]*len(lines_bottom), FONT_TEXT, (0,0,0), 18)
    c.showPage()
    c.save()

# =====================================================
# App UI Styling
# =====================================================
st.set_page_config(page_title="Jomar Valve Submittal Creator", layout="wide")

# Custom CSS for UI and Jomar branding
st.markdown("""
<style>
    section[data-testid="stSidebar"] { background-color: #f9f9f9 !important; }
    .stButton>button, .stDownloadButton>button { background-color: #BC141B !important; color: white !important; border-radius: 3px !important; }
    div[data-testid="stHorizontalBlock"] div.stButton > button { background-color: #f9f9f9 !important; color: #000 !important; border: 1px solid #d3d3d3 !important; }
    .model-entry { margin-bottom: 8px; }
    .model-entry strong { font-size: 1.05rem; }
    .model-entry .model-desc { color: #444; font-size: 0.9rem; }
</style>
""", unsafe_allow_html=True)

# --- Layout Header ---
col1, col2 = st.columns([3, 1], vertical_alignment="center")
with col1:
    st.markdown('<h1 style="margin-top:-5rem;">JOMAR VALVE SUBMITTAL PACKAGE CREATOR</h1>', unsafe_allow_html=True)
    st.write("Upload PDFs and select from the catalog to build your package.")

with col2:
    logo_path = Path(__file__).parent / "Jomar Valve Logo Red.png"
    if logo_path.exists():
        st.image(str(logo_path), width=200)

# ---------------- Session State ----------------
st.session_state.setdefault("queue", [])
st.session_state.setdefault("uploads", [])

# ---------------- Sidebar: Queue ----------------
with st.sidebar:
    st.markdown("### Selected Spec Sheets")

    def _item_label(obj):
        if isinstance(obj, dict) and "Model" in obj: return str(obj["Model"]).strip()
        name = getattr(obj, "name", "")
        return os.path.splitext(name)[0].strip() if name else "Unknown"

    if not st.session_state.queue:
        st.info("No items selected yet.")
    else:
        st.markdown("**Manage Items:**")
        # --- DELETE LOGIC ---
        for i, item in enumerate(list(st.session_state.queue)):
            lbl = _item_label(item)
            c_label, c_del = st.columns([5, 1])
            c_label.markdown(f"• {lbl}")
            if c_del.button("X", key=f"del_{i}", help=f"Remove {lbl}"):
                st.session_state.queue.pop(i)
                if "generated_pdf" in st.session_state: del st.session_state["generated_pdf"]
                st.rerun()

        st.markdown("---")
        # --- SORT LOGIC ---
        labels = [_item_label(x) for x in st.session_state.queue]
        st.markdown("**Click & Drag to Reorder:**")
        sorted_labels = sort_items(labels, direction="vertical", key=f"sort_{len(labels)}")
        
        # Rebuild queue if order changed
        if sorted_labels != labels:
            new_queue = []
            temp_pool = list(st.session_state.queue)
            for sl in sorted_labels:
                for j, obj in enumerate(temp_pool):
                    if _item_label(obj) == sl:
                        new_queue.append(temp_pool.pop(j))
                        break
            st.session_state.queue = new_queue
            st.rerun()

    st.markdown("---")
    if st.button("Clear All Files", use_container_width=True):
        st.session_state.queue.clear()
        st.session_state.uploads.clear()
        if "generated_pdf" in st.session_state: del st.session_state["generated_pdf"]
        st.rerun()

    # --- Create / Download ---
    if st.session_state.queue:
        st.markdown("---")
        if st.button("Create Submittal Package", type="primary", use_container_width=True):
            with st.spinner("Processing..."):
                cover_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                make_cover_pdf(
                    cover_tmp.name, str(logo_path),
                    st.session_state.get("project_name", ""),
                    st.session_state.get("project_location", ""),
                    st.session_state.get("selected_role", ""),
                    st.session_state.get("party_name", ""),
                    st.session_state.get("date_prepared"),
                    st.session_state.get("bid_date"),
                    st.session_state.get("bid_date_tbc", False),
                    st.session_state.get("bid_date_na", False)
                )
                merger = PdfMerger()
                merger.append(cover_tmp.name)
                for f in st.session_state.queue:
                    f.seek(0)
                    merger.append(f)
                
                out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                merger.write(out_tmp.name)
                merger.close()
                with open(out_tmp.name, "rb") as f:
                    st.session_state["generated_pdf"] = f.read()
                st.toast("✅ Package Ready")

        if "generated_pdf" in st.session_state:
            st.download_button("Download PDF", data=st.session_state["generated_pdf"], file_name="Submittal_Package.pdf", mime="application/pdf", use_container_width=True)

# ---------------- Main Form Content ----------------
st.subheader("COVER PAGE DETAILS")
selected_role = role_checkbox_group(key_prefix="aud")
st.session_state["selected_role"] = selected_role
st.text_input("Company Name", key="party_name")
st.text_input("Project Name", key="project_name")
st.text_input("Project Location", key="project_location")
st.date_input("Date Prepared", key="date_prepared")
bd_date, bd_tbc, bd_na = bid_date_picker_with_flags("Bid Date", key="bd")
st.session_state.update({"bid_date": bd_date, "bid_date_tbc": bd_tbc, "bid_date_na": bd_na})

st.markdown("---")
st.subheader("ADD FILES")
uploaded = st.file_uploader("Upload custom PDFs", type="pdf", accept_multiple_files=True)
if uploaded:
    for f in uploaded:
        if f.name not in [getattr(x, "name", "") for x in st.session_state.queue]:
            st.session_state.queue.append(f)
    st.rerun()

# ---------------- Catalog View ----------------
st.subheader("SPEC SHEET LIBRARY")
EXCEL_PATH = "spec_links_images.xlsx"

@st.cache_data
def load_library(path):
    df = pd.read_excel(path)
    return df.dropna(subset=["Model","URL"]).copy()

try:
    library = load_library(EXCEL_PATH)
    cats = sorted(library["Category"].unique())
    c1, c2 = st.columns(2)
    cat = c1.selectbox("Category", cats)
    sub_df = library[library["Category"] == cat]
    subcats = sorted(sub_df["Subcategory"].unique())
    subcat = c2.selectbox("Subcategory", subcats)

    filtered = sub_df[sub_df["Subcategory"] == subcat]
    for _, row in filtered.iterrows():
        col_img, col_txt = st.columns([1, 4])
        with col_img:
            st.image(row["Image"], width=100) if pd.notnull(row["Image"]) else st.write("No Image")
        with col_txt:
            st.markdown(f"**{row['Model']}**\n\n{row.get('Description', '')}")
            if st.button(f"Add {row['Model']}", key=f"btn_{row['Model']}"):
                target_name = f"{row['Model']}.pdf"
                if target_name not in [getattr(x, "name", "") for x in st.session_state.queue]:
                    bytes_data = fetch_pdf_cached(row["URL"])
                    fobj = BytesIO(bytes_data)
                    fobj.name = target_name
                    st.session_state.queue.append(fobj)
                    st.rerun()
except Exception as e:
    st.error(f"Error loading catalog: {e}")
