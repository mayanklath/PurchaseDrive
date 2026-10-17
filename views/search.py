import streamlit as st

from core import analytics as an
from core import services as sv
from core import ui

ui.page_header("🔎 Search Invoices", "Combine any of the filters below. Empty filters are ignored.")
df = ui.get_df()

with st.form("search"):
    text = st.text_input("Quick search", placeholder="Supplier, invoice no, GSTIN, file name or exact amount…")
    with st.expander("More filters", expanded=True):
        c1, c2, c3 = st.columns(3)
        supplier = c1.text_input("Supplier name")
        inv_no = c2.text_input("Invoice number")
        gstin = c3.text_input("Supplier GSTIN")
        c1, c2, c3 = st.columns(3)
        d_from = c1.date_input("Invoice date from", value=None, format="DD/MM/YYYY")
        d_to = c2.date_input("Invoice date to", value=None, format="DD/MM/YYYY")
        fy = c3.selectbox("Financial year (Apr-Mar)", ["Any"] + an.financial_years(df))
        c1, c2, c3 = st.columns(3)
        a_min = c1.number_input("Amount from ₹", value=None, min_value=0.0, step=100.0)
        a_max = c2.number_input("Amount to ₹", value=None, min_value=0.0, step=100.0)
        file_name = c3.text_input("Original file name")
        c1, c2, c3 = st.columns(3)
        payment = c1.multiselect("Payment status", sv.PAYMENT_STATUSES,
                                 format_func=lambda s: sv.PAYMENT_LABELS[s])
        review = c2.selectbox("Review status", ["Any", "Saved", "Needs review"])
        dups = c3.checkbox("Include duplicates", value=True)
    go = st.form_submit_button("🔎 Search", type="primary", width="stretch")

res = an.filter_invoices(
    df, text=text, supplier=supplier, invoice_number=inv_no, gstin=gstin, file_name=file_name,
    date_from=d_from, date_to=d_to, amount_min=a_min, amount_max=a_max,
    financial_year=None if fy == "Any" else fy, payment=payment or None,
    review={"Any": None, "Saved": "confirmed", "Needs review": "pending"}[review],
    include_duplicates=dups)
res = res.sort_values("invoice_date", ascending=False, na_position="last")

counted = an.counted(res)
c1, c2, c3 = st.columns(3)
c1.metric("Bills found", len(res))
c2.metric("Total (no duplicates)", an.inr(counted["total_amount"].sum()))
c3.metric("GST (no duplicates)", an.inr(counted["gst_total"].sum()))
ui.csv_button(res, "search_results", key="search_csv")
ui.invoice_table(res, key="search_table", height=460)
