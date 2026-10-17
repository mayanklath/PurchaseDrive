"""PurchaseDrive AI - start the app with:   streamlit run app.py"""
import streamlit as st

from core import ui
from core.backend import BackendError
from core.config import APP_NAME

st.set_page_config(page_title=APP_NAME, page_icon="🧾", layout="wide",
                   initial_sidebar_state="auto")
ui.inject_css()
backend = ui.get_backend()


# ---------------------------------------------------------------------
#  Login screen
# ---------------------------------------------------------------------
def login_screen() -> None:
    _, mid, _ = st.columns([1, 2, 1])
    with mid:
        st.markdown("<h1 style='text-align:center'>🧾 PurchaseDrive AI</h1>",
                    unsafe_allow_html=True)
        st.markdown("<p style='text-align:center;opacity:.75'>Your private cloud drive for "
                    "purchase bills</p>", unsafe_allow_html=True)
        tab_in, tab_up, tab_reset = st.tabs(["Log in", "Create account", "Forgot password"])

        with tab_in:
            with st.form("login"):
                email = st.text_input("Email", autocomplete="email")
                pw = st.text_input("Password", type="password", autocomplete="current-password")
                ok = st.form_submit_button("Log in", type="primary", width="stretch")
            if ok:
                try:
                    backend.sign_in(email.strip(), pw)
                    st.rerun()
                except BackendError as exc:
                    st.error(str(exc))

        with tab_up:
            with st.form("signup"):
                email = st.text_input("Email", key="su_email")
                pw = st.text_input("Password (min. 8 characters)", type="password", key="su_pw")
                pw2 = st.text_input("Repeat password", type="password", key="su_pw2")
                ok = st.form_submit_button("Create account", type="primary", width="stretch")
            if ok:
                if len(pw) < 8:
                    st.error("Password must be at least 8 characters.")
                elif pw != pw2:
                    st.error("The two passwords are different.")
                else:
                    try:
                        must_confirm = backend.sign_up(email.strip(), pw)
                        if must_confirm:
                            st.success("Account created! Open the confirmation email we sent "
                                       "you, click the link, then log in here.")
                        else:
                            st.success("Account created - you can log in now.")
                    except BackendError as exc:
                        st.error(str(exc))

        with tab_reset:
            st.caption("We email you a 6-digit code. Enter it below with your new password.")
            with st.form("reset1"):
                email = st.text_input("Email", key="rs_email")
                ok = st.form_submit_button("Send code", width="stretch")
            if ok:
                try:
                    backend.send_reset_code(email.strip())
                    st.session_state.reset_email = email.strip()
                    st.success("If that email has an account, a code is on its way.")
                except BackendError as exc:
                    st.error(str(exc))
            with st.form("reset2"):
                email = st.text_input("Email", value=st.session_state.get("reset_email", ""),
                                      key="rs_email2")
                code = st.text_input("6-digit code from the email")
                pw = st.text_input("New password (min. 8 characters)", type="password")
                ok = st.form_submit_button("Set new password", type="primary", width="stretch")
            if ok:
                if len(pw) < 8:
                    st.error("Password must be at least 8 characters.")
                else:
                    try:
                        backend.reset_password_with_code(email.strip(), code, pw)
                        st.success("Password changed - you are now logged in.")
                        st.rerun()
                    except BackendError as exc:
                        st.error(str(exc))


user = backend.current_user()
if user is None:
    login_screen()
    st.stop()

# ---------------------------------------------------------------------
#  Logged in: navigation
# ---------------------------------------------------------------------
P = ui.PAGES
pages = {
    "Overview": [
        st.Page(P["dashboard"], title="Dashboard", icon="📊", default=True),
        st.Page(P["upload"], title="Upload Purchase Bills", icon="📤"),
    ],
    "My Drive": [
        st.Page(P["all"], title="All Purchase Bills", icon="🗂️"),
        st.Page(P["suppliers"], title="Supplier-wise Folders", icon="🏢"),
        st.Page(P["folders"], title="Year & Month Folders", icon="📅"),
        st.Page(P["search"], title="Search Invoices", icon="🔎"),
        st.Page(P["details"], title="Invoice Details", icon="📄"),
    ],
    "Account": [st.Page(P["settings"], title="Settings & Account", icon="⚙️")],
}
nav = st.navigation(pages)

with st.sidebar:
    st.markdown(f"**🧾 {APP_NAME}**")
    st.caption(f"Logged in as {user.email}")
    pending = sum(1 for r in ui.get_rows() if r.get("review_status") == "pending")
    if pending:
        st.warning(f"{pending} bill(s) waiting for review")
    c1, c2 = st.columns(2)
    if c1.button("🔄 Refresh", width="stretch"):
        ui.refresh_rows()
        st.rerun()
    if c2.button("Log out", width="stretch"):
        ui.logout()
        st.rerun()

ui.flash()
nav.run()
