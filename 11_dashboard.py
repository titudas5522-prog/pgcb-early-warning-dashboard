import streamlit as st
import pandas as pd
from dateutil import parser
import urllib.request
import urllib.parse
import urllib.error
import json

# ==========================================
# 1. PAGE SETUP & TITLE
# ==========================================
st.set_page_config(page_title="Intelligent Early Warning System", layout="wide")
st.title("⚡ Intelligent Early Warning System")
st.markdown("Grid Code 2023 & Real-Time Data Driven Load Shedding Risk Analysis")

# ==========================================
# 2. LOAD DATA
# ==========================================
@st.cache_data
def load_data():
    df = pd.read_csv("real_pgcb_data.csv")
    def parse_date_flexible(x):
        try:
            return parser.parse(str(x), dayfirst=True)
        except:
            return pd.NaT
    df['datetime'] = df['datetime'].apply(parse_date_flexible)
    return df.dropna(subset=['datetime'])

try:
    df = load_data()
except Exception as e:
    st.error(f"Error loading data: {e}")
    st.stop()

# ==========================================
# 3. SIDEBAR - LIVE GRID PARAMETERS
# ==========================================
st.sidebar.header("🎛️ Live Parameter Simulation")
freq_input = st.sidebar.slider("Frequency [Hz]", 48.0, 52.0, 49.8, 0.01)
volt_input = st.sidebar.slider("Voltage [kV]", 100.0, 150.0, 132.0, 0.1)
load_input = st.sidebar.slider("Active Power [MW]", -60.0, 10.0, -25.0, 0.1)
curr_input = st.sidebar.slider("Current [A]", 0.0, 350.0, 120.0, 1.0)

# ==========================================
# 4. INDIVIDUAL PARAMETER ASSESSMENT
# ==========================================
def assess_frequency(freq):
    if freq < 49.0 or freq > 51.0: return "Critical", "red", "Emergency Limit Breached"
    elif freq < 49.5 or freq > 50.5: return "High", "orange", "Outside nominal range (49.5 - 50.5 Hz)"
    elif freq < 49.6 or freq > 50.4: return "Warning", "goldenrod", "Approaching frequency limits"
    return "Normal", "green", "Within standard range (49.6 - 50.4 Hz)"

def assess_voltage(volt):
    if volt < 120.0 or volt > 145.0: return "Critical", "red", "Emergency Limit Breached"
    elif volt < 124.08 or volt > 139.92: return "Warning", "goldenrod", "Outside nominal range (124.08 - 139.92 kV)"
    return "Normal", "green", "Within nominal range (124.08 - 139.92 kV)"

def assess_load(load):
    if load < -45.0: return "Critical", "red", "Extreme load stress"
    elif load < -40.0: return "High", "orange", "High load flow"
    elif load < -35.0: return "Warning", "goldenrod", "Rising load flow"
    return "Normal", "green", "Normal load parameters (Range: > -35 MW)"

def assess_current(curr):
    if curr > 200.0: return "Critical", "red", "Line overloading risk"
    elif curr > 175.0: return "High", "orange", "High current level"
    elif curr > 150.0: return "Warning", "goldenrod", "Current rising"
    return "Normal", "green", "Safe current levels (Range: < 150 A)"

f_status, f_color, f_desc = assess_frequency(freq_input)
v_status, v_color, v_desc = assess_voltage(volt_input)
l_status, l_color, l_desc = assess_load(load_input)
c_status, c_color, c_desc = assess_current(curr_input)

# ==========================================
# 5. COMBINED RISK LOGIC
# ==========================================
all_risks = [f_status, v_status, l_status, c_status]

if "Critical" in all_risks:
    combined_risk = "CRITICAL"
    combined_color = "red"
elif "High" in all_risks:
    combined_risk = "HIGH RISK"
    combined_color = "orange"
elif "Warning" in all_risks:
    combined_risk = "WARNING"
    combined_color = "goldenrod"
else:
    combined_risk = "NORMAL"
    combined_color = "green"

# ==========================================
# 6. DISPLAY: COMBINED RISK
# ==========================================
st.markdown("---")
st.markdown(f"<h1 style='text-align: center; color: {combined_color}; border: 3px solid {combined_color}; padding: 15px; border-radius: 10px;'>COMBINED RISK: {combined_risk}</h1>", unsafe_allow_html=True)

if combined_risk in ["CRITICAL", "HIGH RISK"]:
    st.markdown("<h3 style='text-align: center; color: red;'>IMMEDIATE SMS ALERT TRIGGERED</h3>", unsafe_allow_html=True)
elif combined_risk == "WARNING":
    st.markdown("<h3 style='text-align: center; color: goldenrod;'>DASHBOARD NOTIFICATION</h3>", unsafe_allow_html=True)
else:
    st.markdown("<h3 style='text-align: center; color: green;'>GRID HEALTHY</h3>", unsafe_allow_html=True)
    
st.markdown("---")

# ==========================================
# 7. DISPLAY: INDIVIDUAL RISK
# ==========================================
st.subheader("📊 Individual Parameter Assessment")
col1, col2, col3, col4 = st.columns(4)

col1.metric(label="Frequency [Hz]", value=f"{freq_input:.2f}")
col1.markdown(f"<h4 style='color: {f_color}; text-align: center;'>{f_status}</h4>", unsafe_allow_html=True)
col1.caption(f_desc)

col2.metric(label="Voltage [kV]", value=f"{volt_input:.2f}")
col2.markdown(f"<h4 style='color: {v_color}; text-align: center;'>{v_status}</h4>", unsafe_allow_html=True)
col2.caption(v_desc)

col3.metric(label="Active Power [MW]", value=f"{load_input:.2f}")
col3.markdown(f"<h4 style='color: {l_color}; text-align: center;'>{l_status}</h4>", unsafe_allow_html=True)
col3.caption(l_desc)

col4.metric(label="Current [A]", value=f"{curr_input:.2f}")
col4.markdown(f"<h4 style='color: {c_color}; text-align: center;'>{c_status}</h4>", unsafe_allow_html=True)
col4.caption(c_desc)

st.markdown("---")

# ==========================================
# 8. AUTOMATIC SMS ALERT SYSTEM
# ==========================================
import urllib.request
import urllib.parse
import urllib.error
import json

def sms_api_call(path, params, method="POST"):
    encoded = urllib.parse.urlencode(params)
    if method == "POST":
        request = urllib.request.Request(
            "https://api.sms.net.bd" + path, data=encoded.encode("utf-8"),
            headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    else:
        request = urllib.request.Request(
            "https://api.sms.net.bd" + path + "?" + encoded, method="GET")
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

# ==========================================
# AUTOMATIC SMS TRIGGER LOGIC
# ==========================================
st.subheader("📱 Automatic SMS Alert System")

# তোমার ফোন নাম্বারগুলো এখানে লিখে দাও (যত খুশি তত নাম্বার দিতে পারো)
RECIPIENTS = [
    "8801581231492",  # তোমার নাম্বার
    "8801759335310",  # আরেকজনের নাম্বার (এখানে পরিবর্তন করো)
    "8801745119792"   # আরেকজনের নাম্বার (এখানে পরিবর্তন করো)
]

API_KEY = "aENv71GgsFH684eSQs71jFyrnw422xgc6xhHDgH2"

# চেক করা হচ্ছে রিস্ক High বা Critical কিনা
if combined_risk in ["CRITICAL", "HIGH RISK"]:
    
    # মেসেজ তৈরি (লিংক ছাড়া, শুধু ডেটা)
    alert_msg = (
        f"GRID ALERT: {combined_risk.split()[0]} Load Shedding Risk at Khulshi Substation "
        f"(Zone: Khulshi, GEC, Nasirabad). V:{volt_input}kV F:{freq_input}Hz Load:{load_input}MW. "
        f"Action Required."
    )
    
    st.warning(f"⚠️ {combined_risk} detected! Sending automatic SMS to {len(RECIPIENTS)} numbers...")
    
    # প্রতিটি নাম্বারে SMS পাঠানো হচ্ছে
    for number in RECIPIENTS:
        with st.spinner(f"Sending to {number}..."):
            result = sms_api_call(
                "/sendsms",
                {"api_key": API_KEY, "msg": alert_msg, "to": number},
                method="POST"
            )
            
            if result.get("error") == 0:
                st.success(f"✅ SMS Sent to {number}!")
            else:
                st.error(f"❌ Failed to send to {number}: {result.get('msg')}")

else:
    st.info("✅ System is in Normal or Warning state. No SMS will be sent.")
    st.markdown(f"**Current Combined Risk:** {combined_risk}")
