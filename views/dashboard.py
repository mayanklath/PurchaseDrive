import streamlit as st

from core import analytics as an
from core import ui

ui.page_header("📊 Dashboard", "Saved, reviewed bills only. Duplicate copies are never counted twice.")
df = ui.get_df()
m = an.dashboard_metrics(df)

if m["pending_review"]:
    c1, c2 = st.columns([3, 1])
    c1.warning(f"**{m['pending_review']}** uploaded bill(s) are waiting for you to check the "
               "details. They are safe in the cloud but not yet counted below.")
    if c2.button("Review now →", type="primary", width="stretch"):
        st.switch_page(ui.PAGES["upload"])

c1, c2, c3, c4 = st.columns(4)
c1.metric("Invoices saved", f"{m['invoices']:,}")
c2.metric("Total purchases", an.inr(m["purchase_total"]))
c3.metric("Total GST recorded", an.inr(m["gst_total"]))
c4.metric("Balance due (unpaid)", an.inr(m["balance_due"]),
          help="Unpaid + partially paid bills: total minus amount paid")

c1, c2, c3, c4 = st.columns(4)
c1.metric("Paid bills", m["paid_count"], help=an.inr(m["paid_amount"]))
c2.metric("Unpaid / partly paid", m["unpaid_count"])
c3.metric("Payment not set", m["unknown_payment"])
c4.metric("Duplicates ignored", m["duplicates"])

if df.empty:
    st.info("👋 Welcome! Start by uploading your first purchase bill.")
    if st.button("📤 Upload bills", type="primary"):
        st.switch_page(ui.PAGES["upload"])
    st.stop()

left, right = st.columns([3, 2])
with left:
    st.subheader("Monthly purchases")
    mt = an.monthly_totals(df)
    if mt.empty:
        st.caption("No dated invoices yet.")
    else:
        st.bar_chart(mt.set_index("month")[["total"]].rename(columns={"total": "Purchases ₹"}),
                     height=280)
        with st.expander("Monthly table"):
            st.dataframe(mt.rename(columns={"month": "Month", "total": "Purchases ₹",
                                            "gst": "GST ₹", "invoices": "Bills"}),
                         hide_index=True, width="stretch")
with right:
    st.subheader("Top suppliers")
    stt = an.supplier_totals(df)
    if stt.empty:
        st.caption("No saved invoices yet.")
    else:
        st.dataframe(
            stt.rename(columns={"supplier": "Supplier", "invoices": "Bills", "total": "Total ₹",
                                "gst": "GST ₹", "due": "Due ₹"})[["Supplier", "Bills", "Total ₹", "GST ₹", "Due ₹"]],
            hide_index=True, width="stretch",
            column_config={c: st.column_config.NumberColumn(format="%.2f")
                           for c in ["Total ₹", "GST ₹", "Due ₹"]})

st.subheader("Unpaid & partially paid bills")
c = an.counted(df)
unpaid = c[c["payment_status"].isin(["unpaid", "partially_paid"])].sort_values("invoice_date")
ui.invoice_table(unpaid, key="dash_unpaid", show_folder=False)

st.subheader("Recently uploaded")
recent = df.sort_values("created_at", ascending=False).head(8)
ui.invoice_table(recent, key="dash_recent")
