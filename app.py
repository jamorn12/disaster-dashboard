import streamlit as st
import pandas as pd
import geopandas as gpd
import folium
from folium import plugins
from streamlit_folium import folium_static
import branca.colormap as cm
import gpxpy
import os
import json
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
# ดึงผ่าน ID เพื่อความเสถียรบน Cloud
GPX_ID = '179Xvq-DATFAdoCSYDjpLQoFyPyPB58BV' 

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
@st.cache_data(ttl=3600, show_spinner="กำลังโหลดข้อมูลงานวิจัย...")
def load_all_data():
    def clean_name(text):
        if pd.isna(text): return ""
        return str(text).strip().replace("จ.", "").replace("อ.", "").strip()

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

    zip_data = download_file(SHP_ZIP_ID)
    with zipfile.ZipFile(zip_data) as z:
        z.extractall("temp_shp_amp")
    shp_file = [f for f in os.listdir("temp_shp_amp") if f.endswith('.shp')][0]
    gdf = gpd.read_file(os.path.join("temp_shp_amp", shp_file))
    
    pv_col = 'PV_TN' if 'PV_TN' in gdf.columns else ('PROV_NAMT' if 'PROV_NAMT' in gdf.columns else 'pro_tn')
    ap_col = 'AP_TN' if 'AP_TN' in gdf.columns else ('AMP_NAMT' if 'AMP_NAMT' in gdf.columns else 'am_tn')
    
    gdf['prov_clean'] = gdf[pv_col].apply(clean_name)
    gdf['amp_clean'] = gdf[ap_col].apply(clean_name)
    gdf.loc[gdf['amp_clean'] == 'เมือง', 'amp_clean'] = 'เมือง' + gdf['prov_clean']
    gdf = gdf.to_crs(epsg=4326)
    
    gdf['lat'] = gdf.geometry.centroid.y
    gdf['lon'] = gdf.geometry.centroid.x
    gdf['geometry'] = gdf['geometry'].simplify(0.005, preserve_topology=True)
    
    gpx_pts = []
    try:
        gpx_content = download_file(GPX_ID).getvalue().decode("utf-8")
        gpx = gpxpy.parse(gpx_content)
        for track in gpx.tracks:
            for seg in track.segments:
                for p in seg.points: gpx_pts.append((p.latitude, p.longitude))
    except:
        pass

    return df, gdf, gpx_pts, month_map

try:
    df_raw, gdf_base, gpx_points, month_map = load_all_data()
except Exception as e:
    st.error(f"❌ โหลดข้อมูลล้มเหลว: {e}")
    st.stop()

# --- 4. SIDEBAR & CHARTS ---
st.sidebar.title("🔍 การคัดกรอง & วิเคราะห์")
sel_prov = st.sidebar.selectbox("📍 เลือกจังหวัด", ["ทั้งหมด"] + sorted(df_raw['prov_clean'].unique().tolist()))
amp_list = ["ทั้งหมด"]
if sel_prov != "ทั้งหมด":
    amp_list += sorted(df_raw[df_raw['prov_clean'] == sel_prov]['amp_clean'].unique().tolist())
sel_amp = st.sidebar.selectbox("🏘️ เลือกอำเภอ", amp_list)
sel_month = st.sidebar.selectbox("📅 เลือกเดือน", ["ทั้งหมด"] + [month_map[m] for m in sorted(df_raw['เดือน'].unique())])

st.sidebar.markdown("---")
st.sidebar.subheader("🏆 5 จังหวัดที่เสียหายสูงสุด")
top_prov = df_raw.groupby('prov_clean')['บ้านเสียหายรวม'].sum().nlargest(5)
st.sidebar.bar_chart(top_prov)

if st.sidebar.button("🚪 Log Out"):
    del st.session_state.password_correct
    st.rerun()

# --- 5. PROCESSING ---
dff = df_raw.copy()
if sel_prov != "ทั้งหมด": dff = dff[dff['prov_clean'] == sel_prov]
if sel_amp != "ทั้งหมด": dff = dff[dff['amp_clean'] == sel_amp]
if sel_month != "ทั้งหมด": dff = dff[dff['ชื่อเดือน'] == sel_month]

df_sum = dff.groupby(['prov_clean', 'amp_clean']).agg({'จำนวนครั้งรวม':'sum', 'บ้านเสียหายรวม':'sum'}).reset_index()
gdf_final = gdf_base.merge(df_sum, on=['prov_clean', 'amp_clean'], how='left').fillna(0)

# --- 6. UI LAYOUT ---
st.title("🛡️ Disaster Strategic Intelligence Dashboard")

m1, m2, m3, m4 = st.columns(4)
m1.metric("🌪️ เหตุการณ์", f"{int(dff['จำนวนครั้งรวม'].sum()):,} ครั้ง")
m2.metric("🏠 บ้านเสียหายรวม", f"{int(dff['บ้านเสียหายรวม'].sum()):,} หลัง")
m3.metric("📅 เดือน", sel_month)
m4.metric("📈 Max Damage", f"{int(df_sum['บ้านเสียหายรวม'].max() if not df_sum.empty else 0):,} หลัง")

if sel_amp != "ทั้งหมด" or sel_prov != "ทั้งหมด":
    with st.expander("📝 ข้อความสรุปสถานการณ์ในพื้นที่เลือก", expanded=True):
        txt_place = f"อำเภอ {sel_amp}" if sel_amp != "ทั้งหมด" else f"จังหวัด {sel_prov}"
        max_case = dff.loc[dff['บ้านเสียหายรวม'].idxmax()] if not dff.empty else None
        st.markdown(f"พื้นที่ **{txt_place}** พบเหตุการณ์ **{int(dff['จำนวนครั้งรวม'].sum())} ครั้ง** เสียหาย **{int(dff['บ้านเสียหายรวม'].sum()):,} หลัง** รุนแรงที่สุดเมื่อวันที่ **{max_case['วัน'] if max_case is not None else '-'} {max_case['ชื่อเดือน'] if max_case is not None else '-'}**")

st.markdown("---")
col_map, col_viz = st.columns([2, 1])

with col_map:
    st.subheader("🌐 แผนที่แสดงจุดเกิดเหตุและจุดนำทาง")
    center, zoom = [15.5, 102.8], 7
    if sel_amp != "ทั้งหมด":
        t = gdf_final[gdf_final['amp_clean'] == sel_amp]
        center, zoom = [t['lat'].mean(), t['lon'].mean()], 11
    elif sel_prov != "ทั้งหมด":
        t = gdf_final[gdf_final['prov_clean'] == sel_prov]
        center, zoom = [t['lat'].mean(), t['lon'].mean()], 8

    m = folium.Map(location=center, zoom_start=zoom, tiles=None)
    folium.TileLayer('https://mt1.google.com/vt/lyrs=y&x={x}&y={y}&z={z}', attr='Google Satellite', name='ดาวเทียม').add_to(m)
    folium.TileLayer('cartodbpositron', name='แผนที่ขาว').add_to(m)
    
    # 📊 เลเยอร์ความเสียหายรายอำเภอ (Choropleth)
    layer_choropleth = folium.FeatureGroup(name='📊 ความเสียหายรายอำเภอ', show=True)
    mx = float(gdf_final['บ้านเสียหายรวม'].max() or 1)
    color_scale = ['#fff5f0', '#fcbba1', '#fb6a4a', '#de2d26', '#a50f15']
    cp = cm.LinearColormap(colors=color_scale, vmin=0, vmax=mx)
    folium.GeoJson(gdf_final.__geo_interface__, style_function=lambda f: {
        'fillColor': cp(f['properties'].get('บ้านเสียหายรวม', 0)),
        'color': 'black', 'weight': 0.5, 'fillOpacity': 0.7
    }, tooltip=folium.GeoJsonTooltip(fields=['amp_clean', 'บ้านเสียหายรวม'], aliases=['อำเภอ:', 'เสียหาย:'])).add_to(layer_choropleth)
    layer_choropleth.add_to(m)

    # 🚀 เลเยอร์จุดนำทาง Google Maps
    layer_nav = folium.FeatureGroup(name="🚀 จุดนำทาง Google Maps", show=True)
    show_pts = gdf_final[gdf_final['บ้านเสียหายรวม'] > 0] if sel_prov == "ทั้งหมด" else gdf_final[gdf_final['prov_clean'] == sel_prov]
    for _, row in show_pts.iterrows():
        g_url = f"https://www.google.com/maps/search/?api=1&query={row['lat']},{row['lon']}"
        pop = f"<div style='font-family:Sarabun; min-width:150px;'><b>อ.{row['amp_clean']}</b><br>🏠 เสียหาย: {int(row['บ้านเสียหายรวม'])} หลัง<hr><a href='{g_url}' target='_blank' style='display:block; text-align:center; background:#4285F4; color:white; padding:8px; border-radius:5px; text-decoration:none; font-weight:bold;'>🚀 ไป Google Maps</a></div>"
        folium.CircleMarker([row['lat'], row['lon']], radius=7, color='white', weight=2, fill=True, fill_color='#1A73E8', fill_opacity=1, popup=folium.Popup(pop, max_width=250)).add_to(layer_nav)
    layer_nav.add_to(m)

    # 📍 🌟 เพิ่มเลเยอร์ Waypoints & เส้นทางเชื่อมโยง (Google Maps Route)
    layer_waypoints = folium.FeatureGroup(name="📍 จุด Waypoints & เส้นเชื่อม", show=True)
    waypoints_data = [
        ('ม.นเรศวร (เริ่มต้น)', (100.1965, 16.7467)),
        ('วังน้ำเขียว', (101.9348, 14.4009)),
        ('ตัวเมืองนครราชสีมา', (102.2548, 14.8882)),
        ('พิมาย นครราชสีมา', (102.5643, 15.1820)),
        ('สถานีอุตุฯ สุรินทร์', (103.4960, 14.8757)),
        ('สถานีเรดาร์ อุบลราชธานี (ปลายทาง)', (104.8709, 15.2452)),
    ]
    
    # วาดเส้นสีแดงเชื่อมจุด Waypoints ทั้ง 6
    route_coords = [[c[1], c[0]] for n, c in waypoints_data]
    folium.PolyLine(route_coords, color='#e74c3c', weight=4, opacity=0.8, dash_array='10').add_to(layer_waypoints)
    

    # 📍 เลเยอร์สถานที่สำคัญเดิม
    layer_nu = folium.FeatureGroup(name="📍 มหาวิทยาลัยนเรศวร (ม.น.)", show=True)
    folium.Marker([16.7467, 100.1965], popup="<b>มหาวิทยาลัยนเรศวร (ม.น.)</b>", icon=folium.Icon(color="purple", icon="info-sign")).add_to(layer_nu)
    layer_nu.add_to(m)
    
    layer_radar = folium.FeatureGroup(name="📡 สถานีเรดาร์ อุบลราชธานี", show=True)
    folium.Marker([15.2452, 104.8709], popup="<b>สถานีเรดาร์ตรวจอากาศ อุบลราชธานี</b>", icon=folium.Icon(color="orange", icon="info-sign")).add_to(layer_radar)
    layer_radar.add_to(m)

    # 🛣️ เลเยอร์เส้นทางสำรวจ (GPX เส้นสีฟ้า)
    layer_route = folium.FeatureGroup(name="🛣️ เส้นทางสำรวจ", show=True)
    if gpx_points:
        folium.PolyLine(gpx_points, color='#3498db', weight=6, opacity=0.8).add_to(layer_route)
        plugins.AntPath(gpx_points, color='#ffffff', weight=2).add_to(layer_route)
    layer_route.add_to(m)

    folium.LayerControl(collapsed=False).add_to(m)
    
    # Legend
    legend_html = '''
    {% macro html(this, kwargs) %}
    <div style="position: absolute; bottom: 50px; right: 50px; width: 120px; height: 180px; 
        background-color: white; border:2px solid grey; z-index:9999; font-size:12px; padding: 10px; border-radius: 5px; opacity: 0.9;">
        <b>ความเสียหาย (หลัง)</b><br>
        <div style="height: 100px; width: 20px; background: linear-gradient(to top, #fff5f0, #fcbba1, #fb6a4a, #de2d26, #a50f15); float: left; margin-right: 10px; border: 1px solid #ccc;"></div>
        <div style="height: 100px; display: flex; flex-direction: column; justify-content: space-between;">
            <span>''' + str(int(mx)) + '''</span>
            <span>''' + str(int(mx * 0.75)) + '''</span>
            <span>''' + str(int(mx * 0.5)) + '''</span>
            <span>''' + str(int(mx * 0.25)) + '''</span>
            <span>0</span>
        </div>
    </div>
    {% endmacro %}
    '''
    macro = MacroElement()
    macro._template = Template(legend_html)
    m.get_root().add_child(macro)

    folium_static(m, width=850, height=520)

with col_viz:
    st.subheader("📊 สถิติรายวัน")
    daily = dff.groupby('วัน')['จำนวนครั้งรวม'].sum().reset_index()
    st.plotly_chart(px.bar(daily, x='วัน', y='จำนวนครั้งรวม', color_discrete_sequence=['#4285F4']).update_layout(height=250), use_container_width=True)
    
    st.subheader("📈 แนวโน้มรายเดือน")
    trend = dff.groupby('เดือน')['บ้านเสียหายรวม'].sum().reset_index()
    st.plotly_chart(px.line(trend, x='เดือน', y='บ้านเสียหายรวม', markers=True).update_layout(height=200), use_container_width=True)

st.subheader("📝 ตารางข้อมูลละเอียด")
st.dataframe(dff[['วัน', 'ชื่อเดือน', 'จังหวัด_ย่อ', 'อำเภอ_ย่อ', 'บ้านเสียหายรวม']].sort_values('บ้านเสียหายรวม', ascending=False), use_container_width=True)
