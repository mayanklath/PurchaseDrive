import streamlit as st

from core import analytics as an
from core import ui

ui.page_header("🗂️ All Purchase Bills", "Every bill in your cloud drive.")
df = ui.get_df()

c1, c2, c3 = st.columns([2, 2, 1])
show = c1.segmented_control(
    "Show", ["All", "Paid", "Unpaid", "Partially paid", "Needs review", "Duplicates"],
    default="All", key="all_filter")
sort = c2.selectbox("Sort by", ["Newest upload", "Invoice date (new → old)",
                                "Invoice date (old → new)", "Amount (high → low)", "Supplier A-Z"])

view = df
if show == "Paid":
    view = an.counted(df)[an.counted(df)["payment_status"] == "paid"]
elif show == "Unpaid":
    view = an.counted(df)[an.counted(df)["payment_status"] == "unpaid"]
elif show == "Partially paid":
    view = an.counted(df)[an.counted(df)["payment_status"] == "partially_paid"]
elif show == "Needs review":
    view = df[df["review_status"] == "pending"]
elif show == "Duplicates":
    view = df[df["is_duplicate"]]

sorters = {
    "Newest upload": ("created_at", False), "Invoice date (new → old)": ("invoice_date", False),
    "Invoice date (old → new)": ("invoice_date", True), "Amount (high → low)": ("total_amount", False),
    "Supplier A-Z": ("supplier_name", True),
}
col, asc = sorters[sort]
view = view.sort_values(col, ascending=asc, na_position="last")

c = an.counted(view)
st.caption(f"{len(view)} bill(s) shown · total {an.inr(c['total_amount'].sum())} · "
           f"GST {an.inr(c['gst_total'].sum())} (duplicates & unreviewed bills not counted)")
with c3:
    st.write("")
    ui.csv_button(view, "purchase_bills", key="all_csv")
ui.invoice_table(view, key="all_table", height=520)
