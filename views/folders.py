import streamlit as st

from core import analytics as an
from core import ui
from core.organizer import MONTHS

ui.page_header("📅 Year & Month Folders",
               "Browse your drive like folders: PurchaseDrive / Year / Month / Supplier.")
df = ui.get_df()
if df.empty:
    st.info("Your drive is empty. Upload a bill first.")
    st.stop()

tree = an.year_month_tree(df)


def year_key(y: str):
    return (0, -int(y)) if y.isdigit() else (1, y)


years = sorted(tree.keys(), key=year_key)
year = st.segmented_control("📁 PurchaseDrive /", years, default=years[0], key="fold_year")
if not year:
    st.stop()

months = sorted(tree[year].keys(), key=lambda m: MONTHS.index(m) if m in MONTHS else -1,
                reverse=True)
for month in months:
    suppliers = tree[year][month]
    n = sum(len(v) for v in suppliers.values())
    ids = [r["id"] for rows in suppliers.values() for r in rows]
    sub = df[df["id"].isin(ids)]
    total = an.counted(sub)["total_amount"].sum()
    title = f"📂 {month or year}  ·  {n} bill(s)  ·  {an.inr(total)}" if month else \
        f"📂 {year}  ·  {n} bill(s)"
    with st.expander(title, expanded=len(months) == 1):
        st.markdown(f'<span class="pd-folder">PurchaseDrive/{year}{"/" + month if month else ""}</span>',
                    unsafe_allow_html=True)
        for supplier in sorted(suppliers.keys(), key=str.lower):
            sids = [r["id"] for r in suppliers[supplier]]
            st.markdown(f"**📁 {supplier or '(no sub-folder)'}** - {len(sids)} bill(s)")
            ui.invoice_table(df[df["id"].isin(sids)], key=f"fold_{year}_{month}_{supplier}",
                             show_folder=False)
