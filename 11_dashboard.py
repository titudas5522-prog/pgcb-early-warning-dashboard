"""
Script 11: Streamlit Dashboard for Real-Time Outage Risk Monitoring
Run with: streamlit run 11_dashboard.py

Loads the trained XGBoost model and predicts outage risk from the current readings.
The Alert Center works in two modes:
  - Dry-run (always available): builds the SMS and shows exactly what would be sent.
  - Real SMS (only when gateway credentials are configured as Streamlit secrets):
    an operator enters a password, confirms, and the SMS is sent through the
    sms.net.bd gateway. Safety limits apply (recipient cap, cooldown, total cap).

Secrets (never put these in the code or in GitHub):
  SMS_API_KEY, ALERT_PASSWORD, RECIPIENTS
"""

import hmac
import json
import math
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import html
import streamlit as st
import pandas as pd
import joblib
from datetime import datetime, timezone, timedelta

# Bangladesh Standard Time (UTC+6). Cloud servers run on UTC, so we set the offset explicitly.
BD_TIME = timezone(timedelta(hours=6))
DASHBOARD_LINK_TEXT = "outage-warning-bd.streamlit.app"
SMS_API_BASE = "https://api.sms.net.bd"

# ---- Safety limits for REAL SMS (edit here if needed) ----
MAX_RECIPIENTS_PER_SEND = 2      # at most this many people per click
MAX_SMS_TOTAL = 4                # total real SMS allowed while the app instance is running
COOLDOWN_SECONDS = 60            # minimum gap between two real sends
REPORT_WAIT_SECONDS = 5          # wait before asking the gateway for the delivery report
COST_PER_SMS_BDT = 0.40          # measured from the first test SMS

# Demo recipient list for dry-run (placeholders only - no real phone numbers)
DEMO_RECIPIENTS = [
    {"Name": "Demo Resident 1", "Role": "Resident", "Phone": "01XXXXXXXXX"},
    {"Name": "Demo Resident 2", "Role": "Resident", "Phone": "01XXXXXXXXX"},
    {"Name": "Demo Resident 3", "Role": "Resident", "Phone": "01XXXXXXXXX"},
    {"Name": "Demo Shop Owner", "Role": "Commercial", "Phone": "01XXXXXXXXX"},
    {"Name": "Demo Grid Operator", "Role": "Operator", "Phone": "01XXXXXXXXX"},
]


# ==================== Helper functions ====================
def count_sms_segments(text):
    """Approximate SMS segments: 160 chars (GSM-7) or 70 chars (Unicode) per single SMS."""
    if all(ord(ch) < 128 for ch in text):
        return 1 if len(text) <= 160 else math.ceil(len(text) / 153)
    return 1 if len(text) <= 70 else math.ceil(len(text) / 67)


def normalize_number(raw):
    """Accept 01XXXXXXXXX, 8801XXXXXXXXX or +8801XXXXXXXXX; return 8801XXXXXXXXX or None."""
    cleaned = re.sub(r"[\s-]", "", str(raw))
    match = re.fullmatch(r"(?:\+?880|0)(1[3-9]\d{8})", cleaned)
    return "880" + match.group(1) if match else None


def mask_number(number):
    number = str(number)
    return number[:5] + "****" + number[-2:] if len(number) >= 8 else "********"


def build_sms(risk_percent, area, time_label, voltage, frequency, load_percent):
    """Build the alert text. The project name is always included (gateway rule).
    Optional parts are dropped, in order, so that the SMS stays within 160 characters."""
    prefix = (f"Intelligent Early Warning System: HIGH outage risk "
              f"{risk_percent:.0f}% in {area} at {time_label}.")
    readings_full = f" V={voltage:.1f}V F={frequency:.2f}Hz Load={load_percent:.0f}%."
    readings_short = f" V={voltage:.1f}V Load={load_percent:.0f}%."
    link = f" Details: {DASHBOARD_LINK_TEXT}"
    for parts in ((prefix, readings_full, link), (prefix, readings_short, link),
                  (prefix, readings_short), (prefix,)):
        text = "".join(parts)
        if len(text) <= 160:
            return text
    return prefix[:160]


def load_sms_settings():
    """Read gateway credentials from Streamlit secrets.
    Returns (settings, problems). settings is None when real SMS cannot be enabled.
    problems is a list of plain-language hints; secret values are never shown."""
    try:
        names = ("SMS_API_KEY", "ALERT_PASSWORD", "RECIPIENTS")
        present = {name: name in st.secrets for name in names}
    except Exception as exc:
        if "parsing" in str(exc).lower():
            return None, ["A secrets file was found but could not be read. Check that every value "
                          "is inside straight double quotes (\"...\"), not curly quotes, and that "
                          "RECIPIENTS looks like [\"01XXXXXXXXX\"]."]
        return None, ["No secrets were found. On a laptop: create .streamlit/secrets.toml inside the "
                      "folder where you run 'streamlit run', then stop and start Streamlit again. "
                      "On Streamlit Cloud: app Settings > Secrets."]

    problems = []
    api_key = str(st.secrets["SMS_API_KEY"]).strip() if present["SMS_API_KEY"] else ""
    password = str(st.secrets["ALERT_PASSWORD"]).strip() if present["ALERT_PASSWORD"] else ""
    if not api_key:
        problems.append("SMS_API_KEY is missing or empty.")
    if not password:
        problems.append("ALERT_PASSWORD is missing or empty.")

    numbers = []
    if not present["RECIPIENTS"]:
        problems.append("RECIPIENTS is missing.")
    else:
        raw = st.secrets["RECIPIENTS"]
        if isinstance(raw, str):
            raw = [raw]
        try:
            raw = list(raw)
        except TypeError:
            raw = []
        numbers = [n for n in (normalize_number(x) for x in raw) if n]
        if not numbers:
            problems.append("RECIPIENTS has no valid Bangladeshi mobile number. Use real digits "
                            "in the form 01XXXXXXXXX, for example [\"01712345678\"].")
        elif len(numbers) < len(raw):
            problems.append(f"{len(raw) - len(numbers)} number(s) in RECIPIENTS were not valid and are ignored.")

    if not api_key or not password or not numbers:
        return None, problems
    return {"api_key": api_key, "password": password, "numbers": numbers}, problems


def sms_api_call(path, params, method="GET"):
    """Call the gateway. Always returns a dict. Never includes the API key in messages."""
    encoded = urllib.parse.urlencode(params)
    if method == "POST":
        request = urllib.request.Request(
            SMS_API_BASE + path, data=encoded.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    else:
        request = urllib.request.Request(SMS_API_BASE + path + "?" + encoded, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            raw = response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            result = json.loads(raw)
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass
        return {"error": exc.code, "msg": "Gateway returned an HTTP error."}
    except (urllib.error.URLError, TimeoutError):
        return {"error": "network", "msg": "Could not reach the SMS gateway."}
    try:
        result = json.loads(raw)
        return result if isinstance(result, dict) else {"error": "format", "msg": "Unexpected response."}
    except json.JSONDecodeError:
        return {"error": "format", "msg": "Gateway returned invalid data."}


@st.cache_resource
def sms_guard():
    """Process-wide counters shared by all visitors of this app instance."""
    return {"last_sent": 0.0, "sent_total": 0}


# ==================== Page ====================
st.set_page_config(
    page_title="Intelligent Early Warning System",
    page_icon="⚡",
    layout="wide"
)

@st.cache_resource
def load_model():
    model = joblib.load("xgboost_outage_model.pkl")
    feature_columns = joblib.load("feature_columns.pkl")
    return model, feature_columns

model, feature_columns = load_model()

st.title("⚡ Intelligent Early Warning System")
st.subheader("Power Outage Risk Prediction Dashboard")
st.markdown("---")

# ==== Sidebar: Input Panel ====
st.sidebar.header("Current Grid & Weather Readings")

area_name = st.sidebar.text_input("Monitored area", "Chattogram")

voltage = st.sidebar.slider("Voltage (V)", 180.0, 260.0, 230.0, 0.1)
current = st.sidebar.slider("Measured Current (A)", 0.0, 100.0, 20.0, 0.1)
frequency = st.sidebar.slider("Frequency (Hz)", 47.0, 52.0, 50.0, 0.01)
load_percent = st.sidebar.slider("Equipment Loading (%)", 0.0, 150.0, 60.0, 1.0)

st.sidebar.markdown("---")
st.sidebar.subheader("Engineering Reference Settings")
nominal_voltage = st.sidebar.number_input("Nominal LV Voltage (V)", min_value=1.0, value=230.0, step=1.0,
    help="230 V single-phase reference. Change this if your measurement point uses another voltage level.")
rated_current = st.sidebar.number_input("Rated Current of monitored equipment (A)", min_value=0.1, value=30.0, step=1.0,
    help="30 A is only a demonstration default. Enter the actual feeder/transformer/consumer equipment rating.")
voltage_tolerance_pct = st.sidebar.number_input("Voltage tolerance (%)", min_value=1.0, max_value=20.0, value=10.0, step=1.0,
    help="Configurable LV monitoring band; not a load-shedding relay setting.")
current_warning_pct = st.sidebar.number_input("Current warning threshold (%) of rated current", min_value=1.0, max_value=200.0, value=80.0, step=1.0)
current_critical_pct = st.sidebar.number_input("Current critical threshold (%) of rated current", min_value=1.0, max_value=250.0, value=100.0, step=1.0)
load_warning_pct = st.sidebar.number_input("Load warning threshold (%)", min_value=1.0, max_value=200.0, value=80.0, step=1.0)
load_critical_pct = st.sidebar.number_input("Load critical threshold (%)", min_value=1.0, max_value=250.0, value=100.0, step=1.0)
temperature_c = st.sidebar.slider("Temperature (°C)", 10.0, 45.0, 28.0, 0.5)
humidity_percent = st.sidebar.slider("Humidity (%)", 20.0, 100.0, 70.0, 1.0)
wind_speed_ms = st.sidebar.slider("Wind Speed (m/s)", 0.0, 20.0, 3.0, 0.1)
precipitation_mm = st.sidebar.slider("Precipitation (mm)", 0.0, 50.0, 0.0, 0.5)

st.sidebar.markdown("---")
st.sidebar.subheader("Recent History (for trend features)")
voltage_lag_1h = st.sidebar.slider("Voltage 1h ago (V)", 190.0, 250.0, 220.0, 0.1)
voltage_lag_2h = st.sidebar.slider("Voltage 2h ago (V)", 190.0, 250.0, 220.0, 0.1)
voltage_lag_3h = st.sidebar.slider("Voltage 3h ago (V)", 190.0, 250.0, 220.0, 0.1)
load_lag_1h = st.sidebar.slider("Load 1h ago (%)", 0.0, 100.0, 60.0, 1.0)
load_lag_2h = st.sidebar.slider("Load 2h ago (%)", 0.0, 100.0, 60.0, 1.0)
load_lag_3h = st.sidebar.slider("Load 3h ago (%)", 0.0, 100.0, 60.0, 1.0)

now_bd = datetime.now(BD_TIME)
current_hour = now_bd.hour
current_month = now_bd.month

# ==== Derived features (same logic as preprocessing script) ====
weather_stress_index = (
    max(temperature_c - 25, 0) * 0.02 +
    max(humidity_percent - 70, 0) * 0.01 +
    wind_speed_ms * 0.05
)
voltage_deviation = ((voltage - 220) / 220) * 100
frequency_deviation = ((frequency - 50) / 50) * 100
load_trend = load_percent - load_lag_1h

input_dict = {
    "voltage": voltage,
    "current": current,
    "frequency": frequency,
    "load_percent": load_percent,
    "temperature_c": temperature_c,
    "humidity_percent": humidity_percent,
    "wind_speed_ms": wind_speed_ms,
    "precipitation_mm": precipitation_mm,
    "weather_stress_index": weather_stress_index,
    "voltage_deviation": voltage_deviation,
    "frequency_deviation": frequency_deviation,
    "load_trend": load_trend,
    "voltage_lag_1h": voltage_lag_1h,
    "load_lag_1h": load_lag_1h,
    "voltage_lag_2h": voltage_lag_2h,
    "load_lag_2h": load_lag_2h,
    "voltage_lag_3h": voltage_lag_3h,
    "load_lag_3h": load_lag_3h,
    "hour_of_day": current_hour,
    "month": current_month,
}
input_df = pd.DataFrame([input_dict])[feature_columns]

# ==== Combined ML prediction ====
risk_probability = float(model.predict_proba(input_df)[0][1])
risk_percent = risk_probability * 100

if risk_percent < 30:
    risk_level, risk_color = "LOW", "green"
elif risk_percent < 60:
    risk_level, risk_color = "MEDIUM", "orange"
else:
    risk_level, risk_color = "HIGH", "red"

# ==== Independent parameter assessment ====
voltage_low = nominal_voltage * (1 - voltage_tolerance_pct / 100)
voltage_high = nominal_voltage * (1 + voltage_tolerance_pct / 100)
current_pct = current / rated_current * 100

def classify(value, warning, critical, low_is_bad=True):
    if low_is_bad:
        return "CRITICAL" if value <= critical else ("WARNING" if value <= warning else "NORMAL")
    return "CRITICAL" if value >= critical else ("WARNING" if value >= warning else "NORMAL")

# Grid Code frequency thresholds are transmission/system-operation criteria.
# They are displayed as system-level reference, not as a local relay setting.
if frequency < 49.25:
    frequency_status = "CRITICAL"
elif frequency < 49.35:
    frequency_status = "CRITICAL"
elif frequency < 49.50:
    frequency_status = "WARNING"
else:
    frequency_status = "NORMAL"

if voltage < voltage_low or voltage > voltage_high:
    voltage_status = "CRITICAL" if (voltage < nominal_voltage * 0.90 or voltage > nominal_voltage * 1.10) else "WARNING"
else:
    voltage_status = "NORMAL"
current_status = "CRITICAL" if current_pct >= current_critical_pct else ("WARNING" if current_pct >= current_warning_pct else "NORMAL")
load_status = "CRITICAL" if load_percent >= load_critical_pct else ("WARNING" if load_percent >= load_warning_pct else "NORMAL")

parameter_statuses = {"Voltage": voltage_status, "Current": current_status, "Frequency": frequency_status, "Load": load_status}
critical_parameters = [name for name, status in parameter_statuses.items() if status == "CRITICAL"]
warning_parameters = [name for name, status in parameter_statuses.items() if status == "WARNING"]

# SMS decision: critical individual condition OR combined model HIGH.
# Actual SMS still requires operator password + confirmation in the preserved SMS block.
alert_triggered = bool(critical_parameters) or risk_level == "HIGH"
alert_reasons = []
if critical_parameters:
    alert_reasons.append("Critical individual parameter: " + ", ".join(critical_parameters))
if risk_level == "HIGH":
    alert_reasons.append(f"Combined XGBoost risk is HIGH ({risk_percent:.1f}%)")

# ==== Main Display ====
st.markdown("## System-level Combined Risk")
col1, col2, col3 = st.columns(3)
with col1:
    st.metric("XGBoost Outage-Risk Score", f"{risk_percent:.1f}%")
with col2:
    st.markdown("### Combined Risk Level")
    st.markdown(f"<h1 style='color:{risk_color};'>{risk_level}</h1>", unsafe_allow_html=True)
with col3:
    st.metric("Current Hour (BST)", f"{current_hour}:00")
    st.metric("Current Month", current_month)

st.caption("Model score is based on the existing training labels; it is not a verified probability of a real-world outage.")
st.markdown("---")
st.markdown("## Individual Parameter Risks")
cols = st.columns(4)
parameter_values = {"Voltage": f"{voltage:.1f} V", "Current": f"{current:.1f} A ({current_pct:.1f}% rated)", "Frequency": f"{frequency:.2f} Hz", "Load": f"{load_percent:.1f}%"}
for col, name in zip(cols, parameter_statuses):
    status = parameter_statuses[name]
    with col:
        st.metric(name, parameter_values[name], status)
        if status == "CRITICAL": st.error("CRITICAL")
        elif status == "WARNING": st.warning("WARNING")
        else: st.success("NORMAL")

with st.expander("Threshold references and interpretation", expanded=False):
    st.markdown(f"- LV voltage reference: {nominal_voltage:.1f} V ± {voltage_tolerance_pct:.1f}% (monitoring band only; not a load-shedding command).")
    st.markdown(f"- Current loading: {current_pct:.1f}% of configured rated current ({rated_current:.1f} A). Confirm actual equipment rating before deployment.")
    st.markdown(f"- Frequency: Grid Code action references are below 49.50 Hz (do not increase load), below 49.35 Hz (System Operator takes action for manual/automatic load shedding), and below 49.25 Hz (SCADA opening of outgoing feeder CBs at connection points).")
    st.markdown("- Load warning/critical limits are configurable monitoring assumptions; they are not universal Bangladesh load-shedding thresholds.")

if alert_triggered:
    st.error("🚨 ALERT CONDITION DETECTED — " + "; ".join(alert_reasons))
elif warning_parameters or risk_level == "MEDIUM":
    st.warning("Monitoring warning: " + (", ".join(warning_parameters) if warning_parameters else "Combined model risk is MEDIUM") + ". No critical alert condition currently met.")
else:
    st.success("No critical individual or combined alert condition detected.")

st.markdown("---")

# ==================== Alert Center ====================
st.markdown("---")
st.subheader("Alert Center")
st.caption(
    "Dry-run shows exactly what would be sent. Real SMS is available only to an operator "
    "(password) on a server where the gateway credentials are configured."
)

if "alert_log" not in st.session_state:
    st.session_state["alert_log"] = []

time_label = now_bd.strftime("%H:%M")
alert_message = build_sms(risk_percent, area_name, time_label, voltage, frequency, load_percent)
message_length = len(alert_message)
sms_segments = count_sms_segments(alert_message)
sms_settings, sms_problems = load_sms_settings()

if alert_triggered:
    st.markdown("**1. Prepared alert message**")
    st.code(alert_message, language=None)
    st.caption(
        f"{message_length} characters = {sms_segments} SMS segment(s) per recipient "
        "(approximate: 160 characters per segment for plain text)."
    )

    st.markdown("**2. Recipient routing (demo list, dry-run)**")
    recipients_df = pd.DataFrame(DEMO_RECIPIENTS)
    recipients_df["Delivery status"] = "Dry-run (not sent)"
    st.dataframe(recipients_df, width="stretch")

    st.markdown("**3. Gateway request that would be sent**")
    request_preview = (
        f"POST {SMS_API_BASE}/sendsms   (one request per recipient)\n"
        "Form fields:\n"
        "  api_key = ********   (secret, never shown or stored in code)\n"
        f"  msg     = {alert_message}\n"
        "  to      = 8801XXXXXXXXX"
    )
    st.code(request_preview, language=None)

    st.markdown("**4. What a recipient's phone would show**")
    st.markdown(
        f"""
        <div style="max-width:420px;background:#f2f2f7;border-radius:16px;padding:14px 16px;
                    color:#111;font-family:sans-serif;font-size:15px;line-height:1.4;">
          <div style="font-size:12px;color:#555;margin-bottom:6px;">
            SMS from the gateway &middot; {html.escape(time_label)} (BST)
          </div>
          <div style="background:#e1e1e6;border-radius:12px;padding:10px 12px;">{html.escape(alert_message)}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if st.button("Run dry-run alert (log only, nothing is sent)"):
        st.session_state["alert_log"].append({
            "Time (BST)": now_bd.strftime("%Y-%m-%d %H:%M:%S"),
            "Area": area_name,
            "Risk level": "CRITICAL ALERT",
            "Alert reason": "; ".join(alert_reasons),
            "Outage probability (%)": round(risk_percent, 1),
            "Recipients": len(DEMO_RECIPIENTS),
            "SMS segments per recipient": sms_segments,
            "Mode": "Dry-run (nothing sent)",
            "Message": alert_message,
        })
        st.success(f"Dry-run logged for {len(DEMO_RECIPIENTS)} demo recipients. Nothing was sent.")

    # ---------- Real SMS (operator only) ----------
    st.markdown("---")
    st.markdown("**5. Real SMS (operator only)**")
    if sms_settings is None:
        st.info("Real SMS is switched off on this server. The dashboard stays in dry-run mode.")
        for hint in sms_problems:
            st.caption(f"Why: {hint}")
    else:
        guard = sms_guard()
        remaining_total = MAX_SMS_TOTAL - guard["sent_total"]
        planned = min(len(sms_settings["numbers"]), MAX_RECIPIENTS_PER_SEND)
        st.warning(
            f"This sends REAL SMS to {planned} registered recipient(s). "
            f"Estimated cost: BDT {planned * sms_segments * COST_PER_SMS_BDT:.2f}. "
            f"Real SMS still allowed on this server run: {max(remaining_total, 0)}."
        )
        entered_password = st.text_input("Operator password", type="password", key="alert_password")
        confirmed = st.checkbox("I understand this sends real SMS and uses account balance",
                                key="alert_confirm")
        if st.button("Send REAL SMS now"):
            now_ts = time.time()
            wait_left = COOLDOWN_SECONDS - (now_ts - guard["last_sent"])
            if not hmac.compare_digest(entered_password.encode(), sms_settings["password"].encode()):
                st.error("Wrong password. Nothing was sent.")
            elif not confirmed:
                st.warning("Please tick the confirmation box first. Nothing was sent.")
            elif remaining_total <= 0:
                st.error("The real-SMS limit for this server run has been reached. Nothing was sent.")
            elif wait_left > 0:
                st.warning(f"Please wait {int(wait_left) + 1} more seconds before sending again.")
            else:
                targets = sms_settings["numbers"][:min(MAX_RECIPIENTS_PER_SEND, remaining_total)]
                guard["last_sent"] = now_ts
                rows = []
                with st.spinner("Sending..."):
                    for number in targets:
                        result = sms_api_call(
                            "/sendsms",
                            {"api_key": sms_settings["api_key"], "msg": alert_message, "to": number},
                            method="POST")
                        if result.get("error") == 0:
                            guard["sent_total"] += 1
                            request_id = (result.get("data") or {}).get("request_id")
                            rows.append({"Recipient": mask_number(number), "Gateway answer": "Accepted",
                                         "Request ID": request_id, "Report": "pending", "Charge (BDT)": ""})
                        else:
                            rows.append({"Recipient": mask_number(number),
                                         "Gateway answer": f"Failed: {result.get('msg', 'unknown')} (error {result.get('error')})",
                                         "Request ID": "", "Report": "", "Charge (BDT)": ""})
                    if any(r["Request ID"] != "" for r in rows):
                        time.sleep(REPORT_WAIT_SECONDS)
                        for row in rows:
                            if row["Request ID"] == "":
                                continue
                            report = sms_api_call(
                                f"/report/request/{urllib.parse.quote(str(row['Request ID']), safe='')}/",
                                {"api_key": sms_settings["api_key"]})
                            if report.get("error") == 0:
                                data = report.get("data") or {}
                                recipients = data.get("recipients") or []
                                row["Report"] = (recipients[0].get("status") if recipients
                                                 else data.get("request_status", "unknown"))
                                row["Charge (BDT)"] = (recipients[0].get("charge", "") if recipients
                                                       else data.get("request_charge", ""))
                            else:
                                row["Report"] = "report not available yet"
                accepted = sum(1 for r in rows if r["Gateway answer"] == "Accepted")
                st.session_state["alert_log"].append({
                    "Time (BST)": now_bd.strftime("%Y-%m-%d %H:%M:%S"),
                    "Area": area_name,
                    "Risk level": "CRITICAL ALERT",
                    "Alert reason": "; ".join(alert_reasons),
                    "Outage probability (%)": round(risk_percent, 1),
                    "Recipients": f"{accepted} of {len(rows)} accepted",
                    "SMS segments per recipient": sms_segments,
                    "Mode": "REAL SMS",
                    "Message": alert_message,
                })
                if accepted:
                    st.success(f"{accepted} real SMS accepted by the gateway. Check the recipient phone(s).")
                else:
                    st.error("No SMS was accepted by the gateway. See the details below.")
                st.dataframe(pd.DataFrame(rows), width="stretch")
else:
    st.info("No SMS alert condition met. Individual WARNING states remain visible above; SMS preparation starts only for a CRITICAL individual condition or combined XGBoost HIGH.")

if st.session_state["alert_log"]:
    st.markdown("**Alert log (this session)**")
    log_df = pd.DataFrame(st.session_state["alert_log"])
    st.dataframe(log_df, width="stretch")
    st.download_button(
        "Download alert log (CSV)",
        log_df.to_csv(index=False).encode("utf-8"),
        file_name="alert_log.csv",
        mime="text/csv",
    )
    if st.button("Clear alert log"):
        st.session_state["alert_log"] = []
        st.rerun()

# ==== Feature Values Table ====
st.markdown("---")
st.subheader("Current Input Values")
st.dataframe(input_df.T.rename(columns={0: "Value"}), width="stretch")

# ==== Voltage Trend Chart ====
st.markdown("---")
st.subheader("Voltage Trend (Last 3 Hours vs Now)")
trend_df = pd.DataFrame({
    "Time": ["3h ago", "2h ago", "1h ago", "Now"],
    "Voltage": [voltage_lag_3h, voltage_lag_2h, voltage_lag_1h, voltage]
})
st.line_chart(trend_df.set_index("Time"))

st.markdown("---")
st.caption("Intelligent Early Warning System | Individual parameter assessment + combined XGBoost risk | SMS alert on critical condition")
