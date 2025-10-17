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

# ---- Drag & Drop (Sortables) ----
try:
    from streamlit_sortables import sort_items
except ImportError:
    st.warning("⚠️ streamlit-sortables is not installed. Drag & drop ordering will be disabled.")
    sort_items = lambda items, **kwargs: items  # fallback (just return items unchanged)

# ---- Add this section to enable retries for downloads ----
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

# =====================================================
# Drag & drop ordering (with fallback if package missing)
# =====================================================
def _get_sort_labels_fn():
    try:
        from streamlit_sortables import sort_items
        def sort_labels(labels, key="file_order"):
            st.markdown("**Click & Drag to set the order of spec sheets (Cover page stays first):**")
            return sort_items(labels, direction="vertical", multi_containers=False, key=key)
        return sort_labels
    except Exception:
        pass

    # Fallback: numeric ordering inputs
    def sort_labels(labels, key="file_order"):
        st.info("Drag component not available. Enter desired order numbers (1..N).")
        orders = []
        for i, name in enumerate(labels):
            cols = st.columns([1, 6])
            with cols[0]:
                o = st.number_input(
                    "Order", min_value=1, max_value=len(labels),
                    value=i+1, key=f"ord_{key}_{i}", label_visibility="collapsed"
                )
            with cols[1]:
                st.write(name)
            orders.append((o, name))
        orders.sort(key=lambda t: t[0])
        return [name for _, name in orders]
    return sort_labels

sort_labels = _get_sort_labels_fn()

# =====================================================
# Helpers for cover page (kept from your working version)
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
        if not txt:
            continue
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
    return h

def draw_centered_stack(
    c, x_center, y_center, lines, sizes, font_name, color_rgb, leading=26, letter_spacing=0.0, optical_adjust=0.0
):
    if not lines:
        return
    asc_u = pdfmetrics.getAscent(font_name) / 1000.0
    des_u = abs(pdfmetrics.getDescent(font_name) / 1000.0)
    asc0      = asc_u * sizes[0]
    des_last  = des_u * sizes[-1]
    interline = leading * (len(lines) - 1)
    block_h = asc0 + interline + des_last
    first_baseline_y = y_center + (block_h / 2.0) - asc0 + optical_adjust

    c.setFillColorRGB(*color_rgb)
    for i, (txt, sz) in enumerate(zip(lines, sizes)):
        y = first_baseline_y - i * leading
        c.setFont(font_name, sz)
        if letter_spacing and letter_spacing > 0:
            n_gaps = max(len(txt) - 1, 0)
            base_w = pdfmetrics.stringWidth(txt, font_name, sz)
            w = base_w + letter_spacing * n_gaps
            x_left = x_center - (w / 2.0)
            t = c.beginText()
            t.setTextOrigin(x_left, y)
            t.setFont(font_name, sz)
            try:
                t.setCharSpace(letter_spacing)
            except Exception:
                pass
            t.textLine(txt)
            c.drawText(t)
        else:
            c.drawCentredString(x_center, y, txt)

def format_mdY(d, blank="To Be Confirmed"):
    if not d:
        return blank
    return f"{d.month}/{d.day}/{d.year}"

def role_checkbox_group(key_prefix="role"):
    roles = ["Contractor", "Engineer", "Distributor", "Utility"]
    keys = [f"{key_prefix}_{r.lower()}" for r in roles]
    def _set_only(this_key):
        for k in keys:
            if k != this_key:
                st.session_state[k] = False
    cols = st.columns(len(roles))
    for r, k, col in zip(roles, keys, cols):
        with col:
            st.checkbox(r, key=k, on_change=_set_only, args=(k,))
    for r, k in zip(roles, keys):
        if st.session_state.get(k):
            return r
    return None

def bid_date_picker_with_flags(label: str, key: str):
    tbc_key, na_key = f"{key}_tbc", f"{key}_na"
    def _on_tbc_change():
        if st.session_state.get(tbc_key, False):
            st.session_state[na_key] = False
    def _on_na_change():
        if st.session_state.get(na_key, False):
            st.session_state[tbc_key] = False
    disabled = st.session_state.get(tbc_key, False) or st.session_state.get(na_key, False)
    date_val = st.date_input(label, key=f"{key}_date", disabled=disabled)
    cols = st.columns(2)
    with cols[0]:
        st.checkbox("Bid Date To Be Confirmed", key=tbc_key, on_change=_on_tbc_change)
    with cols[1]:
        st.checkbox("Bid Date Not Applicable",  key=na_key,  on_change=_on_na_change)
    tbc_state = st.session_state.get(tbc_key, False)
    na_state  = st.session_state.get(na_key,  False)
    if tbc_state or na_state:
        date_val = None
    return date_val, tbc_state, na_state

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

    FONT_TITLE = "Helvetica"
    FONT_TEXT  = "Helvetica"

    # Light gray inner border
    border_inset = 36
    c.setLineWidth(1)
    c.setStrokeColorRGB(*hex_to_rgb01("#D9D9D9"))
    c.rect(border_inset, border_inset, width - 2*border_inset, height - 2*border_inset, stroke=1, fill=0)

    # Jomar Red bar
    BAR_COLOR  = "#BC141B"
    bar_rgb    = hex_to_rgb01(BAR_COLOR)
    bar_height = 140
    bar_y      = (height / 2.0) - (bar_height / 2.0)
    bar_top_y  = bar_y + bar_height

    c.setFillColorRGB(*bar_rgb)
    c.setStrokeColorRGB(*bar_rgb)
    c.rect(0, bar_y, width, bar_height, stroke=0, fill=1)

    # Logo between page top and bar top
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

    # Title inside the bar (auto-scaling to fit)
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

    # Bottom centered lines
    c.setFillColorRGB(0, 0, 0)
    bottom_block_y = 140

    role_label  = (party_label or "Recipient").upper()
    company_txt = (party_name or "To Be Confirmed").upper()
    first_line  = f"{role_label}: {company_txt}"

    date_prep_txt = format_mdY(date_prepared, blank="To Be Confirmed").upper()

    lines_bottom = [first_line, f"DATE PREPARED: {date_prep_txt}"]

    if not bid_date_na:
        if bid_date_tbc or not bid_date:
            lines_bottom.append("BID DATE: TO BE CONFIRMED")
        else:
            lines_bottom.append(f"BID DATE: {format_mdY(bid_date).upper()}")

    draw_centered_stack(
        c,
        x_center=width / 2.0,
        y_center=bottom_block_y,
        lines=lines_bottom,
        sizes=[12] * len(lines_bottom),
        font_name=FONT_TEXT,
        color_rgb=(0, 0, 0),
        leading=18,
    )

    c.showPage()
    c.save()

# =====================================================
# App UI
# =====================================================
st.set_page_config(page_title="Jomar Spec Sheet Combiner", layout="wide")
st.title("Jomar Valve Submittal Package Builder")
st.caption("Upload PDFs and/or select from catalog, reorder in the sidebar, then generate a combined PDF with a custom cover.")

# Resolve app dir + default logo path (next to this file)
APP_DIR = Path(__file__).parent
LOGO_FILENAME = "Jomar Valve Logo Red.png"  # update if your file name differs
default_logo_path = str(APP_DIR / LOGO_FILENAME)

# ---------------- Session State ----------------
st.session_state.setdefault("queue", [])     # list of file-like objects; each must have .name
st.session_state.setdefault("uploads", [])   # keep track of uploaded files separately (optional)
st.session_state.setdefault("selected_category", None)
st.session_state.setdefault("selected_subcategory", None)

# ---------------- Cover Page Inputs (must be before sidebar) ----------------
# ---------------- Cover Page Inputs (must be AFTER helper defs and BEFORE sidebar) ----------------
st.markdown("---")
st.subheader("Cover Page")

# 1) Role (mutually exclusive) — store into session_state
st.session_state["selected_role"] = role_checkbox_group(key_prefix="aud")

# 2) Company / Project fields
st.session_state["party_name"] = st.text_input(
    "Company",
    st.session_state.get("party_name", ""),
    key="party_name"
)
st.session_state["project_name"] = st.text_input(
    "Project Name",
    st.session_state.get("project_name", ""),
    key="project_name"
)
st.session_state["project_location"] = st.text_input(
    "Project Location",
    st.session_state.get("project_location", ""),
    key="project_location"
)

# 3) Dates
st.session_state["date_prepared"] = st.date_input(
    "Date Prepared",
    key="date_prepared"
)

# 4) Bid Date picker returns 3 values; store explicitly
bd_date, bd_tbc, bd_na = bid_date_picker_with_flags("Bid Date", key="bd")
st.session_state["bid_date"] = bd_date
st.session_state["bid_date_tbc"] = bd_tbc
st.session_state["bid_date_na"] = bd_na


# ---------------- Cover Page Inputs (must be AFTER helper defs and BEFORE sidebar) ----------------
st.markdown("---")
st.subheader("Cover Page")

# 1) Role (mutually exclusive) — store into session_state
st.session_state["selected_role"] = role_checkbox_group(key

# ---------------- Sidebar: Queue ----------------
# =========================
# Sidebar (auto-expanding queue + generate + download)
# =========================
with st.sidebar:
    st.markdown("Selected Spec Sheets")

    # --- Always rebuild display list ---
    display_rows = []
    for q in st.session_state.queue:
        if isinstance(q, dict) and "Model" in q:
            display_rows.append(f"{q['Model']}")
        elif hasattr(q, "name"):
            clean_name = os.path.splitext(q.name)[0]
            display_rows.append(f"{clean_name}")
    for up in st.session_state.uploads:
        if hasattr(up, "name"):
            clean_name = os.path.splitext(up.name)[0]
            display_rows.append(f"{clean_name}")

    if not display_rows:
        st.info("No items selected yet.")
    else:
        st.markdown("Click & Drag to Reorder")
        list_key = f"queue_sort_{len(display_rows)}"
        sorted_items = sort_items(display_rows, direction="vertical", key=list_key)

        # --- Rebuild queue order ---
        new_queue, new_uploads = [], []
        for entry in sorted_items:
            name = entry.strip()
            match_q = next(
                (q for q in st.session_state.queue
                 if (isinstance(q, dict) and q.get("Model") == name)
                 or (getattr(q, "name", "") == f"{name}.pdf")),
                None
            )
            if match_q:
                new_queue.append(match_q)
            match_up = next((u for u in st.session_state.uploads if os.path.splitext(u.name)[0] == name), None)
            if match_up:
                new_uploads.append(match_up)

        st.session_state.queue = new_queue
        st.session_state.uploads = new_uploads

    st.markdown("---")
    if st.button("Clear All Files", use_container_width=True):
        st.session_state.queue.clear()
        st.session_state.uploads.clear()
        st.toast("All Files Cleared")
        st.rerun()

    # --- Create + Download inside sidebar ---
    if st.session_state.queue or st.session_state.uploads:
        create_btn = st.button("Create Submittal Package", type="primary", use_container_width=True)
        if create_btn:
            with st.spinner("Creating Submittal Package..."):
                cover_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                make_cover_pdf(
                    cover_tmp.name,
                    logo_path=default_logo_path,
                    project_name=st.session_state.get("project_name", ""),
                    project_location=st.session_state.get("project_location", ""),
                    party_label=st.session_state.get("selected_role", ""),
                    party_name=st.session_state.get("party_name", ""),
                    date_prepared=st.session_state.get("date_prepared", datetime.now().date()),
                    bid_date=st.session_state.get("bid_date"),
                    bid_date_tbc=st.session_state.get("bid_date_tbc", False),
                    bid_date_na=st.session_state.get("bid_date_na", False),
                )

                merger = PdfMerger()
                merger.append(cover_tmp.name)
                for f in st.session_state.queue:
                    try:
                        merger.append(f)
                    except Exception as e:
                        st.warning(f"Could not add {getattr(f, 'name', 'file')}: {e}")

                out_tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".pdf")
                merger.write(out_tmp.name)
                merger.close()

                with open(out_tmp.name, "rb") as f:
                    st.session_state["generated_pdf"] = f.read()

                st.toast("✅ Submittal Package Created Successfully")

        if "generated_pdf" in st.session_state:
            st.download_button(
                "Download Submittal Package",
                data=st.session_state["generated_pdf"],
                file_name="Jomar Valve Submittal Package.pdf",
                mime="application/pdf",
                use_container_width=True
            )

# ---------------- Uploader ----------------
st.subheader("Upload Spec Sheets")
uploaded_files = st.file_uploader(
    "Add PDF spec sheets (these will appear in the sidebar queue):",
    type="pdf",
    accept_multiple_files=True
)

if uploaded_files:
    # Avoid duplicates by (name, size)
    existing = {(getattr(f, "name", ""), getattr(f, "size", None)) for f in st.session_state.queue}
    new_count = 0
    for f in uploaded_files:
        key = (f.name, getattr(f, "size", None))
        if key not in existing:
            st.session_state.queue.append(f)
            st.session_state.uploads.append(f)
            existing.add(key)
            new_count += 1
    if new_count:
        st.success(f"✓ Added {new_count} uploaded file(s) to queue.")

# ---------------- Catalog View ----------------
st.markdown("---")
st.subheader("Catalog View — Select Spec Sheets from Jomar Library")

EXCEL_PATH = "spec_links_images.xlsx"

@st.cache_data(show_spinner=False)
def load_library(xlsx_path):
    df = pd.read_excel(xlsx_path)
    expected = {"Category","Subcategory","Model","Description","URL","Image"}
    missing = [c for c in expected if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns in Excel: {missing}")
    return df.dropna(subset=["Model","URL"]).copy()

try:
    library = load_library(EXCEL_PATH)
except Exception as e:
    st.error(f"Unable to load Excel: {e}")
    st.stop()

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
        c1, c2 = st.columns([1, 3])
        with c1:
            try:
                st.image(row["Image"], width=110)
            except Exception:
                st.write("No image")
        with c2:
            model = str(row["Model"])
            url   = str(row["URL"])
            desc  = str(row.get("Description", "") or "")
            st.markdown(f"[**{model}**]({url})  \n{desc}")

            add_key = f"add::{category}::{subcategory}::{model}"
            if st.button(f"Add {model}", key=add_key):
                # Deduplicate by filename
                target_name = f"{model}.pdf"
                queue_names = {getattr(f, "name", "") for f in st.session_state.queue}
                if target_name in queue_names:
                    st.toast(f"{model} is already in the queue.", icon="⚠️")
                else:
                    try:
                        resp = requests.get(url, timeout=60)
                        resp.raise_for_status()
                        fobj = BytesIO(resp.content)
                        fobj.name = target_name
                        st.session_state.queue.append(fobj)
                        st.toast(f"✓ Added {model} to queue", icon="✅")
                        st.rerun()   # <— this line forces sidebar to refresh
                    except Exception as e:
                        st.warning(f"Could not add {model}: {e}")


