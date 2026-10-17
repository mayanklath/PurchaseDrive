import streamlit as st

from core import analytics as an
from core import ui

ui.page_header("🏢 Supplier-wise Folders", "All bills grouped by supplier.")
df = ui.get_df()
saved = df[df["review_status"] == "confirmed"].copy()
if saved.empty:
    st.info("No saved bills yet. Upload and save a bill first.")
    st.stop()

saved["supplier"] = saved["supplier_name"].fillna("Other Suppliers")
totals = an.supplier_totals(df)
suppliers = sorted(saved["supplier"].unique(), key=str.lower)

q = st.text_input("🔎 Find a supplier", placeholder="Type part of the name…")
if q.strip():
    suppliers = [s for s in suppliers if q.strip().lower() in s.lower()]
if not suppliers:
    st.warning("No supplier matches that name.")
    st.stop()

sel = st.selectbox(f"Supplier folder ({len(suppliers)})", suppliers,
                   format_func=lambda s: f"📁 {s}  ({int((saved['supplier'] == s).sum())} bills)")
bills = saved[saved["supplier"] == sel].sort_values("invoice_date", ascending=False, na_position="last")
row = totals[totals["supplier"] == sel]
gstins = ", ".join(sorted(bills["supplier_gstin"].dropna().unique())) or "—"

c1, c2, c3, c4 = st.columns(4)
c1.metric("Bills", int(row["invoices"].iloc[0]) if not row.empty else 0)
c2.metric("Total purchases", an.inr(row["total"].iloc[0] if not row.empty else 0))
c3.metric("GST", an.inr(row["gst"].iloc[0] if not row.empty else 0))
c4.metric("Balance due", an.inr(row["due"].iloc[0] if not row.empty else 0))
st.caption(f"GSTIN: {gstins}")

ui.csv_button(bills, f"supplier_{sel.replace(' ', '_')}", key="sup_csv")
ui.invoice_table(bills, key="sup_table")

with st.expander("All suppliers - summary table"):
    st.dataframe(totals.rename(columns={"supplier": "Supplier", "invoices": "Bills",
                                        "taxable": "Taxable ₹", "gst": "GST ₹", "total": "Total ₹",
                                        "due": "Due ₹"}),
                 hide_index=True, width="stretch")
