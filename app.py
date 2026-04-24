import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from folium import plugins
from streamlit_folium import folium_static
import branca.colormap as cm
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
import zipfile
import plotly.express as px
from branca.element import MacroElement
from jinja2 import Template

# --- 1. CONFIG & PASSWORD ---
st.set_page_config(page_title="Strategic Disaster Intelligence", layout="wide")

def check_password():
    if "password_correct" not in st.session_state:
        st.title("🔐 Disaster Intelligence Login")
        st.info("ระบบจัดการข้อมูลงานวิจัยพายุ (เข้าถึงเฉพาะบุคคล)")
        password = st.text_input("กรุณากรอกรหัสผ่าน", type="password")
        if st.button("เข้าสู่ระบบ"):
            if password == "041244":
                st.session_state.password_correct = True
                st.rerun()
            else:
                st.error("❌ รหัสผ่านไม่ถูกต้อง")
        return False
    return True

if not check_password():
    st.stop()

# --- 2. DRIVE API SETTINGS ---
CSV_ID = '179Xvq-DATFAdoCSYDjpLQoFyPyPB58BV' 
SHP_ZIP_ID = '1wFrYGQ6gUjhlDAuwfnGe1jIZ5cqU01aE' 

@st.cache_resource(show_spinner=False)
def get_drive_service():
    info = dict(st.secrets["gcp_service_account"])
    if "private_key" in info:
        info["private_key"] = info["private_key"].replace("\\n", "\n")
    creds = service_account.Credentials.from_service_account_info(info)
    return build('drive', 'v3', credentials=creds)

def download_file(file_id):
    service = get_drive_service()
    request = service.files().get_media(fileId=file_id)
    return io.BytesIO(request.execute())

# --- 3. DATA LOADING ---
@st.cache_data(ttl=3600)
def load_all_data():
    def clean_name(text):
        if pd.isna(text): return ""
        return str(text).strip().replace("จ.", "").replace("อ.", "").strip()

    # Load CSV
    df = pd.read_csv(download_file(CSV_ID), encoding='cp874')
    df['บ้านเสียหายรวม'] = pd.to_numeric(df['บ้านเสียหาย \n(หลังคาเรือน)'], errors='coerce').fillna(0)
    df.loc[df['บ้านเสียหายรวม'] >= 9999, 'บ้านเสียหายรวม'] = 0
    df['prov_clean'] = df['จังหวัด_ย่อ'].apply(clean_name)
    df['amp_clean'] = df['อำเภอ_ย่อ'].apply(clean_name)
    df.loc[df['amp_clean'] == 'เมือง', 'amp_clean'] = 'เมือง' + df['prov_clean']
    
    month_map = {1:"มกราคม", 2:"กุมภาพันธ์", 3:"มีนาคม", 4:"เมษายน", 5:"พฤษภาคม", 6:"มิถุนายน",
                 7:"กรกฎาคม", 8:"สิงหาคม", 9:"กันยายน", 10:"ตุลาคม", 11:"พฤศจิกายน", 12:"ธันวาคม"}
    df['ชื่อเดือน'] = df['เดือน'].map(month_map)
    df['จำนวนครั้งรวม'] = 1

    # Load SHP
    zip_data = download_file(SHP_ZIP_ID)
    if not os.path.exists("temp_shp_amp"): os.makedirs("temp_shp_amp")
    with zipfile.ZipFile(zip_data) as z:
        z.extractall("temp_shp_amp")
    shp_file = [f for f in os.listdir("temp_shp_amp") if f.endswith('.shp')][0]
    gdf = gpd.read_file(os.path.join("temp_shp_amp", shp_file))
    
    # 🌟 ตรวจสอบชื่อคอลัมน์ให้ยืดหยุ่นที่สุด
    cols = gdf.columns.tolist()
    pv_col = next((c for c in ['PV_TN', 'PROV_NAMT', 'pro_tn', 'PROV_TN'] if c in cols), cols[0])
    ap_col = next((c for c in ['AP_TN', 'AMP_NAMT', 'am_tn', 'AMP_TN'] if c in cols), cols[1])
    
    gdf['prov_clean'] = gdf[pv_col].apply(clean_name)
    gdf['amp_clean'] = gdf[ap_col].apply(clean_name)
    gdf.loc[gdf['amp_clean'] == 'เมือง', 'amp_clean'] = 'เมือง' + gdf['prov_clean']
    
    gdf = gdf.to_crs(epsg=4326)
    gdf['lat'] = gdf.geometry.centroid.y
    gdf['lon'] = gdf.geometry.centroid.x
    gdf['geometry'] = gdf['geometry'].simplify(0.005)
    
    return df, gdf, month_map

try:
    df_raw, gdf_base, month_map = load_all_data()
except Exception as e:
    st.error(f"❌ โหลดข้อมูลล้มเหลว: {e}")
    st.stop()

# --- 4. SIDEBAR & PROCESSING ---
st.sidebar.title("🔍 คัดกรองข้อมูล")
sel_prov = st.sidebar.selectbox("📍 เลือกจังหวัด", ["ทั้งหมด"] + sorted(df_raw['prov_clean'].unique().tolist()))
amp_list = ["ทั้งหมด"]
if sel_prov != "ทั้งหมด":
    amp_list += sorted(df_raw[df_raw['prov_clean'] == sel_prov]['amp_clean'].unique().tolist())
sel_amp = st.sidebar.selectbox("🏘️ เลือกอำเภอ", amp_list)
sel_month = st.sidebar.selectbox("📅 เลือกเดือน", ["ทั้งหมด"] + [month_map[m] for m in sorted(df_raw['เดือน'].unique())])

dff = df_raw.copy()
if sel_prov != "ทั้งหมด": dff = dff[dff['prov_clean'] == sel_prov]
if sel_amp != "ทั้งหมด": dff = dff[dff['amp_clean'] == sel_amp]
if sel_month != "ทั้งหมด": dff = dff[dff['ชื่อเดือน'] == sel_month]

# รวมข้อมูลเข้ากับแผนที่
df_sum = dff.groupby(['prov_clean', 'amp_clean']).agg({'จำนวนครั้งรวม':'sum', 'บ้านเสียหายรวม':'sum'}).reset_index()
gdf_final = gdf_base.merge(df_sum, on=['prov_clean', 'amp_clean'], how='left').fillna(0)

# --- 5. UI ---
st.title("🛡️ Disaster Intelligence Dashboard")
m1, m2, m3 = st.columns(3)
m1.metric("🌪️ เหตุการณ์", f"{int(dff['จำนวนครั้งรวม'].sum()):,} ครั้ง")
m2.metric("🏠 บ้านเสียหายรวม", f"{int(dff['บ้านเสียหายรวม'].sum()):,} หลัง")
m3.metric("📍 พื้นที่ที่เลือก", sel_amp if sel_amp != "ทั้งหมด" else sel_prov)

st.markdown("---")
col_map, col_viz = st.columns([2, 1])

with col_map:
    st.subheader("🌐 แผนที่วิเคราะห์ความเสียหาย")
    center = [gdf_final['lat'].mean(), gdf_final['lon'].mean()]
    m = folium.Map(location=center, zoom_start=7, tiles='cartodbpositron')
    folium.TileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google', name='ดาวเทียม').add_to(m)
    
    # 📊 Layer: Choropleth
    mx = float(gdf_final['บ้านเสียหายรวม'].max() or 1)
    cp = cm.LinearColormap(colors=['#fff5f0', '#fb6a4a', '#a50f15'], vmin=0, vmax=mx)
    
    folium.GeoJson(
        gdf_final.__geo_interface__,
        style_function=lambda f: {
            'fillColor': cp(f['properties'].get('บ้านเสียหายรวม', 0)),
            'color': 'black', 'weight': 0.5, 'fillOpacity': 0.7
        },
        tooltip=folium.GeoJsonTooltip(fields=['amp_clean', 'บ้านเสียหายรวม'], aliases=['อำเภอ:', 'เสียหาย:'])
    ).add_to(m)

    # 🚀 Layer: Markers
    for _, row in gdf_final[gdf_final['บ้านเสียหายรวม'] > 0].iterrows():
        g_url = f"https://www.google.com/maps/search/?api=1&query={row['lat']},{row['lon']}"
        pop = f"<b>อ.{row['amp_clean']}</b><br>เสียหาย: {int(row['บ้านเสียหายรวม'])} หลัง<br><a href='{g_url}' target='_blank'>🚀 เปิดแผนที่</a>"
        folium.CircleMarker([row['lat'], row['lon']], radius=5, color='blue', fill=True, popup=folium.Popup(pop, max_width=200)).add_to(m)

    folium.LayerControl().add_to(m)
    folium_static(m, width=800, height=500)

with col_viz:
    st.subheader("📊 อันดับความเสียหาย")
    top_5 = df_sum.nlargest(5, 'บ้านเสียหายรวม')
    st.plotly_chart(px.bar(top_5, x='amp_clean', y='บ้านเสียหายรวม', color='บ้านเสียหายรวม'), use_container_width=True)
    st.subheader("📈 แนวโน้มเดือน")
    st.plotly_chart(px.line(dff.groupby('เดือน')['บ้านเสียหายรวม'].sum().reset_index(), x='เดือน', y='บ้านเสียหายรวม'), use_container_width=True)

st.dataframe(dff[['วัน','ชื่อเดือน','จังหวัด_ย่อ','อำเภอ_ย่อ','บ้านเสียหายรวม']].sort_values('บ้านเสียหายรวม', ascending=False), use_container_width=True)
