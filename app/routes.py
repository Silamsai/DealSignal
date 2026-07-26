"""Flask route handlers."""

import logging
import os
import subprocess
import sys
from datetime import datetime

from flask import (
    Blueprint, flash, get_flashed_messages, redirect, render_template,
    render_template_string, request, session, url_for,
)
from werkzeug.security import check_password_hash

from app.auth import (
    authenticate, clear_login_failures, client_ip, get_admin_password_hash,
    login_lock_remaining, login_required, record_login_failure, start_session,
)
from app.config import (
    ADMIN_USERNAME, BASE_DIR, CSV_PATH, DEFAULT_BRAND, HOMEDATA_API_KEY,
    SESSION_HOURS, STANNP_TEST_MODE,
)
from app.models import (
    already_sent, create_user, find_user_by_email, load_brand, load_deals,
    load_leads, load_scan, log_sent, save_admin_password, save_brand,
    save_lead, save_scan, update_user_password,
)
from app.services import (
    flyer_back_message, flyer_front_html, reveal_address, send_postcard,
    stannp_key_ready,
)

logger = logging.getLogger(__name__)

bp = Blueprint("main", __name__)


# ------------------------------------------------------------------ helpers
def _render_dashboard(preview_html=None, preview_back=None):
    get_flashed_messages()  # discard queued flash banners
    return render_template(
        "dashboard.html",
        brand=load_brand(),
        deals=load_deals(),
        sent=already_sent(),
        test_mode=STANNP_TEST_MODE,
        scan_cfg=load_scan(),
        leads=load_leads(),
        preview_html=preview_html,
        preview_back=preview_back,
        display_name=session.get("display_name") or session.get("username") or ADMIN_USERNAME,
        nav="deals",
    )


# ------------------------------------------------------------------ public
@bp.route("/")
def home():
    return render_template("home.html", brand=load_brand())


@bp.route("/healthz")
def health():
    """Health check endpoint for load balancers / uptime monitors."""
    return {"status": "ok"}, 200


@bp.route("/signup", methods=["GET", "POST"])
def signup():
    if request.method == "POST":
        name = (request.form.get("name") or "").strip()
        email = (request.form.get("email") or "").strip().lower()
        password = request.form.get("password") or ""
        confirm = request.form.get("confirm_password") or ""

        if not name or not email or not password:
            flash("Please fill in name, email, and password.", "error")
            return render_template("signup.html")
        if len(password) < 8:
            flash("Password must be at least 8 characters.", "error")
            return render_template("signup.html")
        if password != confirm:
            flash("Password and confirmation do not match.", "error")
            return render_template("signup.html")
        if email == ADMIN_USERNAME.lower() or find_user_by_email(email):
            flash("That email is already registered. Please Login instead.", "error")
            return render_template("signup.html")

        create_user(name, email, password)
        session.clear()
        flash("Account created. Now Login with your email and password.", "message")
        return redirect(url_for("main.login"))
    return render_template("signup.html")


@bp.route("/login", methods=["GET", "POST"])
def login():
    next_url = request.args.get("next") or request.form.get("next") or url_for("main.app_dashboard")

    ip = client_ip()
    lock_secs = login_lock_remaining(ip)
    if lock_secs > 0:
        mins = max(1, (lock_secs + 59) // 60)
        flash(f"Too many failed attempts. Try again in about {mins} minute(s).", "login_error")
        return render_template("login.html", next_url=next_url, login_locked=True)

    if request.method == "POST":
        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")
        ok, payload = authenticate(username, password)
        if ok:
            clear_login_failures(ip)
            start_session(payload)
            logger.info("Successful login: %s from %s", username, ip)
            return redirect(next_url)

        record_login_failure(ip)
        if login_lock_remaining(ip) > 0:
            flash("Too many failed attempts. Login temporarily locked.", "login_error")
            return render_template("login.html", next_url=next_url, login_locked=True)
        flash("Invalid email/username or password.", "login_error")

    return render_template("login.html", next_url=next_url, login_locked=False)


@bp.route("/logout")
def logout():
    session.clear()
    flash("You have logged out.", "message")
    return redirect(url_for("main.home"))


# ------------------------------------------------------------------ app (auth required)
@bp.route("/app")
@login_required
def app_dashboard():
    return _render_dashboard()


@bp.route("/app/account", methods=["GET", "POST"])
@login_required
def account():
    role = session.get("role") or "admin"
    email = session.get("email") or ""
    username = session.get("username") or ADMIN_USERNAME
    display_name = session.get("display_name") or username

    if request.method == "POST":
        current = request.form.get("current_password", "")
        new_pw = request.form.get("new_password", "")
        confirm = request.form.get("confirm_password", "")

        if role == "admin":
            current_ok = check_password_hash(get_admin_password_hash(), current)
        else:
            user = find_user_by_email(email)
            current_ok = bool(user) and check_password_hash(user.get("password_hash") or "", current)

        if not current_ok:
            flash("Current password is incorrect.", "error")
        elif len(new_pw) < 8:
            flash("New password must be at least 8 characters.", "error")
        elif new_pw != confirm:
            flash("New password and confirmation do not match.", "error")
        else:
            if role == "admin":
                save_admin_password(new_pw)
            else:
                update_user_password(email, new_pw)
            flash("Password updated.", "message")
        return redirect(url_for("main.account"))

    return render_template(
        "account.html",
        display_name=display_name, username=username,
        email=email, role=role, session_hours=SESSION_HOURS,
    )


@bp.route("/scan", methods=["POST"])
@login_required
def scan():
    area = (request.form.get("area") or "Bradford").strip()
    max_price = (request.form.get("max_price") or "250000").strip()
    if not HOMEDATA_API_KEY:
        flash("Set HOMEDATA_API_KEY first.", "error")
        return redirect(url_for("main.app_dashboard"))

    finder_path = os.path.join(BASE_DIR, "bin", "motivated_seller_finder.py")
    cmd = [
        sys.executable, finder_path,
        "--area", area, "--max-price", max_price,
        "--deep-dive", "3", "--out", CSV_PATH,
    ]
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=600,
            env={**os.environ, "HOMEDATA_API_KEY": HOMEDATA_API_KEY},
        )
    except subprocess.TimeoutExpired:
        flash("Scan timed out — try a smaller area or higher price filter.", "error")
        return redirect(url_for("main.app_dashboard"))

    if r.returncode != 0:
        err = r.stderr.strip().splitlines()[-1] if r.stderr else "unknown error"
        logger.error("Scan failed for %s: %s", area, r.stderr)
        flash(f"Scan failed: {err}", "error")
        return redirect(url_for("main.app_dashboard"))

    save_scan({
        "area": area, "max_price": max_price,
        "last_run": datetime.now().strftime("%d %b %Y %H:%M"),
    })
    n = len(load_deals())
    flash(f"Scan complete: {area} — {n} motivated-seller candidate(s) found.")
    return redirect(url_for("main.app_dashboard"))


@bp.route("/brand", methods=["POST"])
@login_required
def brand():
    save_brand({k: request.form.get(k, "").strip() for k in DEFAULT_BRAND})
    flash("Brand saved.")
    return redirect(url_for("main.app_dashboard"))


@bp.route("/preview", methods=["POST"])
@login_required
def preview():
    b = load_brand()
    return _render_dashboard(
        preview_html=flyer_front_html(b, None),
        preview_back=flyer_back_message(b),
    )


@bp.route("/send", methods=["POST"])
@login_required
def send():
    ids = request.form.getlist("ids")
    if not ids:
        flash("No properties selected. Tick at least one property first.", "error")
        return redirect(url_for("main.app_dashboard"))
    if not HOMEDATA_API_KEY:
        flash("Homedata API key is missing. Add HOMEDATA_API_KEY to your .env file.", "error")
        return redirect(url_for("main.app_dashboard"))
    if not stannp_key_ready():
        flash(
            "Flyer send failed: Stannp API key is missing or still the placeholder. "
            "Put your real key in .env as STANNP_API_KEY=... then restart the app. "
            "(Preview flyer still works without it.)",
            "error",
        )
        return redirect(url_for("main.app_dashboard"))

    b = load_brand()
    deals = {d["listing_id"]: d for d in load_deals()}
    ok = fail = 0
    last_error = ""
    for lid in ids:
        deal = deals.get(lid)
        if not deal:
            continue
        try:
            addr = reveal_address(lid)
            resp = send_postcard(b, deal, addr)
            sid = resp.get("data", {}).get("id", "?")
            log_sent([
                datetime.now().isoformat(timespec="seconds"), lid,
                f"{addr['address1']}, {addr['postcode']}", sid,
                STANNP_TEST_MODE, "ok",
            ])
            ok += 1
        except Exception as e:
            logger.exception("Failed to send flyer for listing %s", lid)
            last_error = str(e)
            log_sent([
                datetime.now().isoformat(timespec="seconds"), lid,
                "", "", STANNP_TEST_MODE, f"error: {e}",
            ])
            fail += 1

    if fail and not ok:
        reason = last_error
        if "401" in reason:
            reason = "Stannp rejected the API key (401 Unauthorized). Check STANNP_API_KEY in .env."
        flash(f"Could not send flyer(s). {reason}", "error")
    elif fail:
        flash(
            f"{ok} flyer(s) {'proofed in test mode' if STANNP_TEST_MODE else 'sent'}, "
            f"{fail} failed. Last error: {last_error}",
            "error",
        )
    else:
        flash(
            f"{ok} flyer(s) {'proofed in test mode (PDF only — nothing posted)' if STANNP_TEST_MODE else 'sent successfully'}.",
            "message",
        )
    return redirect(url_for("main.app_dashboard"))


# ------------------------------------------------------------------ landing pages
@bp.route("/landing")
def landing():
    return render_template("deal_alerts_landing.html")


@bp.route("/sell")
def sell_landing():
    return render_template("seller_landing.html", brand=load_brand())


@bp.route("/sell-inquiry", methods=["POST"])
def sell_inquiry():
    postcode = request.form.get("postcode", "").strip()
    name = request.form.get("name", "").strip()
    phone = request.form.get("phone", "").strip()
    email = request.form.get("email", "").strip()
    reason = request.form.get("reason", "").strip()

    if not postcode or not name or not phone:
        flash("Please fill out all required fields.", "error")
        return redirect(url_for("main.sell_landing"))

    try:
        save_lead(postcode, name, phone, email, reason)
        flash("Your cash offer request has been received. We will contact you shortly.", "inquiry_success")
    except Exception:
        logger.exception("Error saving seller lead")
        flash("Error saving details. Please try again.", "error")

    return redirect(url_for("main.sell_landing"))


@bp.route("/privacy")
def privacy():
    return render_template("privacy.html", brand=load_brand())

