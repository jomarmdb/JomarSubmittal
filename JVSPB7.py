import streamlit as st
import pandas as pd
import requests
from io import BytesIO
from PyPDF2 import PdfMerger
from datetime import datetime
import tempfile, os, re
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

def make_cover_pdf(
    outfile: str,
    logo_path: str,
    project_name: str,
    project_location: str,
    party_label: str,
    party_name: str,
    date_prepared,
    bid_date,
    bid_date_tbc: bool = False,
    bid_date_na: bool = False,
):
    c = canvas.Canvas(outfile, pagesize=letter)
    width, height = letter

    # Fonts (built-in)
    FONT_TITLE = "Helvetica"
    FONT_TEXT  = "Helvetica"

    # ---- Light gray inner border ----
    border_inset = 36
    c.setLineWidth(1)
    c.setStrokeColorRGB(*hex_to_rgb01("#D9D9D9"))
    c.rect(border_inset, border_inset, width - 2*border_inset, height - 2*border_inset, stroke=1, fill=0)

    # ---- Red bar ----
    BAR_COLOR  = "#BC141B"
    bar_rgb    = hex_to_rgb01(BAR_COLOR)
    bar_height = 140
    bar_y      = (height / 2.0) - (bar_height / 2.0)
    bar_top_y  = bar_y + bar_height

    c.setFillColorRGB(*bar_rgb)
    c.setStrokeColorRGB(*bar_rgb)
    c.rect(0, bar_y, width, bar_height, stroke=0, fill=1)

    # ---- Logo: centered between page top and bar top ----
    if logo_path and os.path.exists(logo_path):
        try:
            draw_logo_centered_between_page_top_and_bar_top(
                c, logo_path, max_width=300,
                page_width=width, page_height=height, bar_top_y=bar_top_y
            )
        except Exception as e:
            st.warning(f"Logo draw error: {e}")
    else:
        st.warning(f"Logo file not found at: {logo_path}")

    # ---- Title inside the bar (auto-scaling to fit) ----
    title_lines = [
        (project_name or "TO BE CONFIRMED").upper(),
        (project_location or "TO BE CONFIRMED").upper(),
        "SUBMITTAL PACKAGE",
    ]
    sizes, dyn_leading = fit_multiline_text(
        lines=title_lines,
        font_name=FONT_TITLE,
        bar_width=width,
        bar_height=bar_height,
        side_pad=48,
        v_pad=18,
        max_pt=30,
        min_pt=14,
        leading_factor=1.12,
        letter_spacing=0.0,
    )
    draw_centered_stack(
        c,
        x_center=width / 2.0,
        y_center=bar_y + bar_height / 2.0,
        lines=title_lines,
        sizes=sizes,
        font_name=FONT_TITLE,
        color_rgb=(1, 1, 1),
        leading=dyn_leading,
    )


    # ---- Bottom text block ----
    c.setFillColorRGB(0, 0, 0)
    c.setFont("Helvetica", 12)
    bottom_y = 140

    contractor_txt = (contractor or "TO BE CONFIRMED").upper()
    dp = date_prepared.strftime("%-m/%-d/%Y") if date_prepared else "TO BE CONFIRMED"
    bd = bid_date.strftime("%-m/%-d/%Y") if bid_date else "TO BE CONFIRMED"

    lines = [
        f"CONTRACTOR: {contractor_txt}",
        f"DATE PREPARED: {dp.upper()}",
        f"BID DATE: {bd.upper()}",
    ]

    line_height = 18
    for i, text in enumerate(lines):
        c.drawCentredString(width / 2, bottom_y - (i * line_height), text)

    c.showPage()
    c.save()


# =========================
# App Configuration
# =========================
st.set_page_config(page_title="Jomar Spec Sheet Combiner", layout="wide")
st.title("Valve Spec Sheet Combiner — Catalog View")
st.caption("Select by Category → Subcategory → Product. Add uploads, manage a queue, and generate a combined PDF with a Jomar-styled cover.")

EXCEL_PATH = "spec_links_images.xlsx"
DEFAULT_LOGO_PATH = r"C:\Users\matt.bianchi\OneDrive - jomar.com\Jomar\Specification Sales\Projects\Spec Package Builder\App\Current2\Jomar Valve Logo Red.png"
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

st.session_state.setdefault("queue", [])
st.session_state.setdefault("uploads", [])

# =========================
# Sidebar: Queue / Cart
# =========================
with st.sidebar:
    st.markdown("Selected Spec Sheets")

    # Build the drag/drop list from current queue + uploads
    display_rows = []
    for q in st.session_state.queue:
        display_rows.append(f"⋮⋮ {q['Model']}")
    for up in st.session_state.uploads:
        display_rows.append(f"⋮⋮ 📄 {up.name}")

    if not display_rows:
        st.info("No items selected yet.")
    else:
        st.markdown("Click & Drag to Reorder")

        # Dynamically render drag/drop list
        sorted_items = sort_items(display_rows, direction="vertical", key="queue_sort_sidebar")

        # Rebuild queue order from sorted items
        new_queue, new_uploads = [], []
        for entry in sorted_items:
            name = entry.replace("⋮⋮", "").strip()
            if name.startswith("📄 "):
                file_name = name.replace("📄 ", "")
                match = next((f for f in st.session_state.uploads if f.name == file_name), None)
                if match:
                    new_uploads.append(match)
            else:
                model = name.strip()
                match = next((q for q in st.session_state.queue if q["Model"] == model), None)
                if match:
                    new_queue.append(match)

        # ✅ Update queue AFTER the for-loop (important!)
        st.session_state.queue = new_queue
        st.session_state.uploads = new_uploads
        st.toast("✅ Selected File Added")

        st.markdown("---")
        if st.button("Clear All Files", use_container_width=True):
            st.session_state.queue.clear()
            st.session_state.uploads.clear()
            st.toast("All Files Cleared")
            st.rerun()

# =========================
# Main Page
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
    st.info("No products found for this selection.")
else:
    for _, row in filtered.iterrows():
        c1, c2 = st.columns([1, 3], vertical_alignment="center")
        with c1:
            try:
                st.image(row["Image"], width=110)
            except Exception:
                st.write("No image")
        with c2:
            model = str(row["Model"])
            url = str(row["URL"])
            desc = str(row.get("Description", "") or "")
            st.markdown(f"[**{model}**]({url})  \n{desc}")

            # ✅ Stable Add button key
            add_key = f"add::{category}::{subcategory}::{model}"
            if st.button(f"Add {model}", key=add_key):
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
                    st.rerun()
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

        for item in st.session_state.queue:
            try:
                resp = requests.get(item["URL"], timeout=30)
                resp.raise_for_status()
                merger.append(BytesIO(resp.content))
            except Exception as e:
                st.warning(f"Could not add {item['Model']}: {e}")

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
