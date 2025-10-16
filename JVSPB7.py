import streamlit as st
import pandas as pd
import requests
from io import BytesIO
from PyPDF2 import PdfMerger
from datetime import datetime
import tempfile, os, uuid, re
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.lib.utils import ImageReader
from streamlit_sortables import sort_items

# =========================
# PDF Cover Page (Jomar style)
# =========================
def hex_to_rgb01(hex_color: str):
    h = hex_color.strip().lstrip("#")
    r = int(h[0:2], 16) / 255.0
    g = int(h[2:4], 16) / 255.0
    b = int(h[4:6], 16) / 255.0
    return r, g, b

def try_register_font(ttf_path: str, face_name: str):
    if ttf_path and os.path.exists(ttf_path):
        try:
            pdfmetrics.registerFont(TTFont(face_name, ttf_path))
            return face_name
        except Exception:
            pass
    return "Helvetica"

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

def make_cover_pdf(outfile: str, logo_path: str, project_name: str, project_location: str,
                   contractor: str, date_prepared, bid_date, font_path_light: str = ""):
    c = canvas.Canvas(outfile, pagesize=letter)
    width, height = letter
    font_light = try_register_font(font_path_light, "ProximaNova-Light")

    # Red bar (lowered & taller)
    BAR_COLOR = "#BC141B"
    bar_rgb = hex_to_rgb01(BAR_COLOR)
    bar_height = 150
    bar_y = (height / 2) - 10
    bar_top_y = bar_y + bar_height

    c.setFillColorRGB(*bar_rgb)
    c.rect(0, bar_y, width, bar_height, stroke=0, fill=1)

    # Logo
    if logo_path and os.path.exists(logo_path):
        draw_logo_centered_between_page_top_and_bar_top(
            c, logo_path, max_width=220, page_width=width, page_height=height, bar_top_y=bar_top_y
        )

    # White stacked text inside bar (ALL CAPS)
    c.setFillColorRGB(1, 1, 1)
    c.setFont(font_light, 24)
    c.drawCentredString(width/2, bar_y + bar_height - 40, (project_name or "PROJECT NAME").upper())
    c.setFont(font_light, 16)
    c.drawCentredString(width/2, bar_y + bar_height - 72, (project_location or "PROJECT LOCATION").upper())
    c.setFont(font_light, 13)
    c.drawCentredString(width/2, bar_y + 22, "SUBMITTAL PACKAGE")

    # Bottom fields
    left_margin = 50
    base_y = 120
    line_gap = 18
    c.setFillColorRGB(0, 0, 0)
    c.setFont(font_light, 11)
    c.drawString(left_margin, base_y + line_gap * 2, f"Contractor: {(contractor or '').strip()}")
    dp = date_prepared.strftime("%B %d, %Y") if date_prepared else ""
    bd = bid_date.strftime("%B %d, %Y") if bid_date else ""
    c.drawString(left_margin, base_y + line_gap, f"Date Prepared: {dp}")
    c.drawString(left_margin, base_y, f"Bid Date: {bd}")
    c.showPage()
    c.save()

# =========================
# App UI / Logic
# =========================
st.set_page_config(page_title="Jomar Spec Sheet Combiner", layout="wide")
st.title("Valve Spec Sheet Combiner — Catalog View")
st.caption("Select by Category → Subcategory → Product. Add uploads, manage queue, and generate a combined PDF with a Jomar-styled cover.")

EXCEL_PATH = "spec_links_images.xlsx"
DEFAULT_LOGO_PATH = r"C:\Users\Matt.Bianchi\OneDrive - jomar.com\Jomar\Company Info\Logos\Jomar Valve Logo Red.png"
PROXIMA_TTF = ""

@st.cache_data(show_spinner=False)
def load_library(xlsx_path):
    df = pd.read_excel(xlsx_path)
    expected = {"Category","Subcategory","Model","Description","URL","Image"}
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {missing}")
    return df.dropna(subset=["Model","URL"]).copy()

try:
    library = load_library(EXCEL_PATH)
except Exception as e:
    st.error(f"Unable to load Excel: {e}")
    st.stop()

# ---- Session state ----
st.session_state.setdefault("queue", [])
st.session_state.setdefault("uploads", [])

# =========================
# Sidebar: Queue / Cart
# =========================
with st.sidebar:
    st.header("🧾 Spec Sheet Queue")

    display_rows = []
    for q in st.session_state.queue:
        display_rows.append(f"⋮⋮ {q['Model']}\u200b{uuid.uuid4().hex[:6]}")
    for up in st.session_state.uploads:
        display_rows.append(f"⋮⋮ 📄 {up.name}\u200b{uuid.uuid4().hex[:6]}")

    if not display_rows:
        st.info("No items in queue yet.")
    else:
        sorted_items = sort_items(display_rows, direction="vertical", key="queue_sort_sidebar")
        new_queue, new_uploads = [], []
        for entry in sorted_items:
            name = re.sub(r"\u200b[0-9a-f]{6}$", "", entry).strip()
            if name.startswith("⋮⋮ 📄 "):
                file_name = name.replace("⋮⋮ 📄 ", "")
                match = next((f for f in st.session_state.uploads if f.name == file_name), None)
                if match:
                    new_uploads.append(match)
            else:
                model = name.replace("⋮⋮ ", "").strip()
                match = next((q for q in st.session_state.queue if q["Model"] == model), None)
                if match:
                    new_queue.append(match)

        st.session_state.queue = new_queue
        st.session_state.uploads = new_uploads

        st.markdown("---")
        if st.button("🗑️ Clear Queue", use_container_width=True):
            st.session_state.queue.clear()
            st.session_state.uploads.clear()
            st.toast("Queue cleared")
            st.rerun()

# =========================
# Main page
# =========================
# ---- Persistent category & subcategory selections ----
if "selected_category" not in st.session_state:
    st.session_state.selected_category = None
if "selected_subcategory" not in st.session_state:
    st.session_state.selected_subcategory = None

cols = st.columns(2)
with cols[0]:
    categories = sorted(library["Category"].dropna().unique())
    category = st.selectbox(
        "Category",
        categories,
        index=categories.index(st.session_state.selected_category)
        if st.session_state.selected_category in categories else 0,
    )
    st.session_state.selected_category = category

with cols[1]:
    sub_df = library[library["Category"] == category]
    subcategories = sorted(sub_df["Subcategory"].dropna().unique())
    subcategory = st.selectbox(
        "Subcategory",
        subcategories,
        index=subcategories.index(st.session_state.selected_subcategory)
        if st.session_state.selected_subcategory in subcategories else 0,
    )
    st.session_state.selected_subcategory = subcategory

filtered = library[(library["Category"] == category) & (library["Subcategory"] == subcategory)]
st.markdown("### Products")

if filtered.empty:
    st.info("No products found.")
else:
    for _, row in filtered.iterrows():
        c1, c2 = st.columns([1, 3], vertical_alignment="center")
        with c1:
            try: st.image(row["Image"], width=110)
            except: st.write("No image")
        with c2:
            model, url, desc = row["Model"], row["URL"], row.get("Description","")
            st.markdown(f"[**{model}**]({url})  \n{desc}")
add_key = f"add_{category}_{subcategory}_{model}_{uuid.uuid4().hex[:6]}"
add_clicked = st.button(f"Add {model}", key=add_key)

if add_clicked:
    if "queue" not in st.session_state:
        st.session_state.queue = []

    if not any(q["Model"] == model for q in st.session_state.queue):
        st.session_state.queue.append({
            "Category": row["Category"],
            "Subcategory": row["Subcategory"],
            "Model": model,
            "Description": desc,
            "URL": url,
            "Image": row["Image"]
        })
        st.toast(f"✓ Added {model}", icon="✅")
    else:
        st.toast(f"{model} is already in the queue.", icon="⚠️")

# ---- Upload PDFs ----
st.markdown("---")
st.subheader("Optional: Drag & Drop Additional PDFs")
uploaded_files = st.file_uploader(
    "Add extra PDFs (merged after the cover)",
    type="pdf",
    accept_multiple_files=True
)
if uploaded_files:
    new_count = 0
    existing_keys = {(f.name, f.size) for f in st.session_state.uploads}
    for f in uploaded_files:
        if (f.name, f.size) not in existing_keys:
            st.session_state.uploads.append(f)
            existing_keys.add((f.name, f.size))
            new_count += 1
    if new_count:
        st.success(f"✓ Added {new_count} uploaded file(s).")

# ---- Cover Page Fields ----
st.markdown("---")
st.subheader("Cover Page")
col1, col2 = st.columns(2)
with col1:
    project_name = st.text_input("Project Name", "")
    contractor_name = st.text_input("Contractor", "")
with col2:
    project_location = st.text_input("Project Location", "")
    date_prepared = st.date_input("Date Prepared")
bid_date = st.date_input("Bid Date")
logo_path = DEFAULT_LOGO_PATH

# ---- Generate Combined PDF ----
if st.session_state.queue or st.session_state.uploads:
    if st.button("Generate Combined PDF", type="primary"):
        cover_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
        make_cover_pdf(
            cover_tmp.name,
            logo_path,
            project_name,
            project_location,
            contractor_name,
            date_prepared,
            bid_date,
            PROXIMA_TTF,
        )

        merger = PdfMerger()
        merger.append(cover_tmp.name)

        # Library PDFs
        for item in st.session_state.queue:
            try:
                resp = requests.get(item["URL"], timeout=30)
                resp.raise_for_status()
                merger.append(BytesIO(resp.content))
            except Exception as e:
                st.warning(f"Could not add {item['Model']}: {e}")

        # Uploaded PDFs
        for up in st.session_state.uploads:
            try:
                merger.append(up)
            except Exception as e:
                st.warning(f"Could not add uploaded file {up.name}: {e}")

        output = BytesIO()
        merger.write(output)
        merger.close()
        output.seek(0)

        st.download_button(
            "⬇️ Download Combined PDF",
            data=output,
            file_name="Combined_Spec_Sheets.pdf",
            mime="application/pdf"
        )
