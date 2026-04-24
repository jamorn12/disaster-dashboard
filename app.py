import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from folium import plugins
from streamlit_folium import folium_static
import branca.colormap as cm
import gpxpy
import os
import io
import zipfile
import plotly.express as px
from google.oauth2 import service_account
from googleapiclient.discovery import build
from branca.element import MacroElement
from jinja2 import Template

# --- 1. CONFIG & LOGIN ---
st.set_page_config(page_title="Strategic Disaster Intelligence", layout="wide")

if "password_correct" not in st.session_state:
    st.title("🔐 Disaster Intelligence Login")
    password = st.text_input("กรุณากรอกรหัสผ่าน", type="password")
    if st.button("เข้าสู่ระบบ"):
        if password == "041244":
            st.session_state.password_correct = True
            st.rerun()
        else:
            st.error("❌ รหัสผ่านไม่ถูกต้อง")
    st.stop()

# --- 2. DRIVE SETTINGS ---
CSV_ID = '179Xvq-DATFAdoCSYDjpLQoFyPyPB58BV' 
SHP_ZIP_ID = '1wFrYGQ6gUjhlDAuwfnGe1jIZ5cqU01aE' 
GPX_PATH = '/content/drive/MyDrive/data Point Pee james/ฐานข้อมูลภาคอีสานย้อนหลัง11 ปี/mapstogpx20260422_194132.gpx'

@st.cache_resource
def get_drive_service():
    info = dict(st.secrets["gcp_service_account"])
    if "private_key" in info: info["private_key"] = info["private_key"].replace("\\n", "\n")
    creds = service_account.Credentials.from_service_account_info(info)
    return build('drive', 'v3', credentials=creds)

def download_file(file_id):
    service = get_drive_service()
    return io.BytesIO(service.files().get_media(fileId=file_id).execute())

# --- 3. DATA LOADING ---
@st.cache_data
def load_all():
    df = pd.read_csv(download_file(CSV_ID), encoding='cp874')
    df['บ้านเสียหายรวม'] = pd.to_numeric(df['บ้านเสียหาย \n(หลังคาเรือน)'], errors='coerce').fillna(0)
    df['prov_clean'] = df['จังหวัด_ย่อ'].str.strip().replace("จ.", "", regex=False)
    df['amp_clean'] = df['อำเภอ_ย่อ'].str.strip().replace("อ.", "", regex=False)
    df.loc[df['amp_clean'] == 'เมือง', 'amp_clean'] = 'เมือง' + df['prov_clean']
    
    with zipfile.ZipFile(download_file(SHP_ZIP_ID)) as z: z.extractall("temp_shp")
    gdf = gpd.read_file([os.path.join("temp_shp", f) for f in os.listdir("temp_shp") if f.endswith('.shp')][0])
    gdf = gdf.to_crs(epsg=4326)
    
    gpx_pts = []
    if os.path.exists(GPX_PATH):
        with open(GPX_PATH, 'r', encoding='utf-8') as f:
            gpx = gpxpy.parse(f)
            for t in gpx.tracks:
                for s in t.segments:
                    for p in s.points: gpx_pts.append((p.latitude, p.longitude))
    return df, gdf, gpx_pts

df_raw, gdf_base, gpx_points = load_all()

# --- 4. MAP & DISPLAY ---
st.title("🛡️ Strategic Disaster Intelligence Dashboard")
col_map, col_viz = st.columns([2, 1])

with col_map:
    # สร้างแผนที่
    m = folium.Map(location=[15.5, 102.8], zoom_start=7, tiles='cartodbpositron')
    folium.TileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google', name='ดาวเทียม').add_to(m)

    # 1. เลเยอร์เส้นทาง GPX (เส้นสีฟ้า)
    if gpx_points:
        lyr_route = folium.FeatureGroup(name="🛣️ เส้นทางสำรวจ (GPX)", show=True).add_to(m)
        folium.PolyLine(gpx_points, color='#3498db', weight=6, opacity=0.8).add_to(lyr_route)
        plugins.AntPath(gpx_points, color='#ffffff', weight=2).add_to(lyr_route)

    # 2. เลเยอร์ปักหมุด 6 จุด (Waypoints)
    lyr_wp = folium.FeatureGroup(name="📍 สถานที่สำคัญ (6 จุด)", show=True).add_to(m)
    waypoints = {
        'ม.นเรศวร (Start)': (16.7467, 100.1965),
        'วังน้ำเขียว': (14.4009, 101.9348),
        'นครราชสีมา': (14.8882, 102.2548),
        'พิมาย': (15.1820, 102.5643),
        'สุรินทร์': (14.8757, 103.4960),
        'เรดาร์ อุบลฯ (End)': (15.2452, 104.8709)
    }
    for name, pos in waypoints.items():
        folium.Marker(pos, popup=name, icon=folium.Icon(color='red' if 'End' in name else 'blue', icon='info-sign')).add_to(lyr_wp)

    # 3. เลเยอร์ความเสียหายรายอำเภอ
    df_sum = df_raw.groupby(['prov_clean', 'amp_clean'])['บ้านเสียหายรวม'].sum().reset_index()
    gdf_final = gdf_base.merge(df_sum, on=['prov_clean', 'amp_clean'], how='left').fillna(0)
    mx = float(gdf_final['บ้านเสียหายรวม'].max() or 1)
    cp = cm.LinearColormap(colors=['#fff5f0', '#fb6a4a', '#a50f15'], vmin=0, vmax=mx)
    folium.GeoJson(gdf_final.__geo_interface__, style_function=lambda f: {
        'fillColor': cp(f['properties'].get('บ้านเสียหายรวม', 0)),
        'color': 'black', 'weight': 0.5, 'fillOpacity': 0.6
    }).add_to(m)

    folium.LayerControl().add_to(m)
    folium_static(m, width=800, height=550)

with col_viz:
    st.subheader("📊 ข้อมูลจังหวัด")
    st.bar_chart(df_raw.groupby('จังหวัด_ย่อ')['บ้านเสียหายรวม'].sum().nlargest(5))
    st.dataframe(df_raw[['วัน','อำเภอ_ย่อ','บ้านเสียหายรวม']].sort_values('บ้านเสียหายรวม', ascending=False))
