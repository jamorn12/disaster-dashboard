import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from folium import plugins
from streamlit_folium import folium_static
import branca.colormap as cm
import gpxpy
import os
from google.oauth2 import service_account
from googleapiclient.discovery import build
import io
import zipfile
import plotly.express as px
from branca.element import MacroElement
from jinja2 import Template

# --- 1. CONFIG ---
st.set_page_config(page_title="Strategic Disaster Intelligence", layout="wide")

if "password_correct" not in st.session_state:
    st.title("🔐 Disaster Intelligence Login")
    password = st.text_input("รหัสผ่าน", type="password")
    if st.button("เข้าสู่ระบบ"):
        if password == "041244":
            st.session_state.password_correct = True
            st.rerun()
        else: st.error("รหัสผ่านไม่ถูกต้อง")
    st.stop()

# --- 2. DRIVE API ---
CSV_ID = '179Xvq-DATFAdoCSYDjpLQoFyPyPB58BV' 
SHP_ZIP_ID = '1wFrYGQ6gUjhlDAuwfnGe1jIZ5cqU01aE'
# 📍 แก้ไขตรงนี้: ใส่ File ID ของไฟล์ GPX ของคุณ
GPX_ID = '179Xvq-DATFAdoCSYDjpLQoFyPyPB58BV' # <-- เปลี่ยนเป็น ID ไฟล์ GPX จริงๆ

@st.cache_resource
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

# --- 3. LOAD DATA ---
@st.cache_data(ttl=3600)
def load_all_data():
    def clean(t): return str(t).strip().replace("จ.", "").replace("อ.", "").strip() if pd.notna(t) else ""

    # CSV
    df = pd.read_csv(download_file(CSV_ID), encoding='cp874')
    df['บ้านเสียหายรวม'] = pd.to_numeric(df['บ้านเสียหาย \n(หลังคาเรือน)'], errors='coerce').fillna(0)
    df['prov_clean'] = df['จังหวัด_ย่อ'].apply(clean)
    df['amp_clean'] = df['อำเภอ_ย่อ'].apply(clean)
    df.loc[df['amp_clean'] == 'เมือง', 'amp_clean'] = 'เมือง' + df['prov_clean']
    df['ชื่อเดือน'] = df['เดือน'].map({1:"มกราคม", 2:"กุมภาพันธ์", 3:"มีนาคม", 4:"เมษายน", 5:"พฤษภาคม", 6:"มิถุนายน", 7:"กรกฎาคม", 8:"สิงหาคม", 9:"กันยายน", 10:"ตุลาคม", 11:"พฤศจิกายน", 12:"ธันวาคม"})

    # SHP
    with zipfile.ZipFile(download_file(SHP_ZIP_ID)) as z: z.extractall("temp_shp")
    shp = [f for f in os.listdir("temp_shp") if f.endswith('.shp')][0]
    gdf = gpd.read_file(os.path.join("temp_shp", shp))
    pv_c = next(c for c in ['PV_TN', 'PROV_NAMT', 'pro_tn'] if c in gdf.columns)
    ap_c = next(c for c in ['AP_TN', 'AMP_NAMT', 'am_tn'] if c in gdf.columns)
    gdf['prov_clean'], gdf['amp_clean'] = gdf[pv_c].apply(clean), gdf[ap_c].apply(clean)
    gdf.loc[gdf['amp_clean'] == 'เมือง', 'amp_clean'] = 'เมือง' + gdf['prov_clean']
    gdf = gdf.to_crs(epsg=4326)
    gdf['lat'], gdf['lon'] = gdf.geometry.centroid.y, gdf.geometry.centroid.x

    # 🌟 GPX (เส้นสีฟ้า) - ดึงจาก Drive ID
    gpx_pts = []
    try:
        gpx_data = download_file(GPX_ID).getvalue().decode("utf-8")
        gpx = gpxpy.parse(gpx_data)
        for t in gpx.tracks:
            for s in t.segments:
                for p in s.points: gpx_pts.append((p.latitude, p.longitude))
    except: pass

    return df, gdf, gpx_pts

df_raw, gdf_base, gpx_points = load_all_data()

# --- 4. MAP & UI ---
st.title("🛡️ Disaster Intelligence Dashboard")
sel_prov = st.sidebar.selectbox("จังหวัด", ["ทั้งหมด"] + sorted(df_raw['prov_clean'].unique().tolist()))
dff = df_raw.copy()
if sel_prov != "ทั้งหมด": dff = dff[dff['prov_clean'] == sel_prov]

df_sum = dff.groupby(['prov_clean', 'amp_clean'])['บ้านเสียหายรวม'].sum().reset_index()
gdf_final = gdf_base.merge(df_sum, on=['prov_clean', 'amp_clean'], how='left').fillna(0)

m = folium.Map(location=[15.5, 102.8], zoom_start=7, tiles=None)
folium.TileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google', name='ดาวเทียม').add_to(m)
folium.TileLayer('cartodbpositron', name='แผนที่ขาว').add_to(m)

# 🔵 เลเยอร์เส้นสีฟ้า (AntPath)
if gpx_points:
    layer_route = folium.FeatureGroup(name="🛣️ เส้นทางสำรวจ (เส้นสีฟ้า)", show=True)
    folium.PolyLine(gpx_points, color='#3498db', weight=6, opacity=0.8).add_to(layer_route)
    plugins.AntPath(gpx_points, color='#ffffff', weight=2).add_to(layer_route)
    layer_route.add_to(m)

# 📊 เลเยอร์ความเสียหาย
mx = float(gdf_final['บ้านเสียหายรวม'].max() or 1)
cp = cm.LinearColormap(colors=['#fff5f0', '#fb6a4a', '#a50f15'], vmin=0, vmax=mx)
folium.GeoJson(gdf_final.__geo_interface__, style_function=lambda f: {
    'fillColor': cp(f['properties'].get('บ้านเสียหายรวม', 0)),
    'color': 'black', 'weight': 0.5, 'fillOpacity': 0.7
}).add_to(m)

folium.LayerControl().add_to(m)
folium_static(m, width=1000, height=600)
st.dataframe(dff)
