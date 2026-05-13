# ---------------- Sidebar: Queue ----------------
with st.sidebar:
    st.markdown("### Selected Spec Sheets")

    # Helper to get clean labels
    def _item_label(obj):
        if isinstance(obj, dict) and "Model" in obj:
            return str(obj["Model"]).strip()
        name = getattr(obj, "name", "")
        return os.path.splitext(name)[0].strip() if name else "Unknown File"

    if not st.session_state.queue:
        st.info("No items selected yet.")
    else:
        # 1. DELETE SECTION
        st.markdown("**Manage Items:**")
        
        # Create a copy of the queue to iterate over while potentially modifying the original
        for i, item in enumerate(list(st.session_state.queue)):
            lbl = _item_label(item)
            cols = st.columns([5, 1])
            with cols[0]:
                st.markdown(f"**{lbl}**")
            with cols[1]:
                # Use a unique key for every delete button
                if st.button("X", key=f"del_{lbl}_{i}", help=f"Remove {lbl}"):
                    st.session_state.queue.pop(i)
                    # Clear generated PDF because the list changed
                    if "generated_pdf" in st.session_state:
                        del st.session_state["generated_pdf"]
                    st.rerun()

        st.markdown("---")
        
        # 2. REORDER SECTION
        # We use the labels for the drag-and-drop UI
        current_labels = [_item_label(x) for x in st.session_state.queue]
        
        st.markdown("**Click & Drag to Reorder:**")
        list_key = f"queue_sort_{len(current_labels)}"
        sorted_labels = sort_items(current_labels, direction="vertical", key=list_key)

        # Rebuild queue based on the new order of labels
        if sorted_labels != current_labels:
            new_queue = []
            temp_pool = list(st.session_state.queue)
            for label in sorted_labels:
                for j, obj in enumerate(temp_pool):
                    if _item_label(obj) == label:
                        new_queue.append(temp_pool.pop(j))
                        break
            st.session_state.queue = new_queue
            st.rerun()

    st.markdown("---")
    if st.button("Clear All Files", use_container_width=True):
        st.session_state.queue.clear()
        st.session_state.uploads.clear()
        if "generated_pdf" in st.session_state:
            del st.session_state["generated_pdf"]
        st.toast("All Files Cleared")
        st.rerun()
