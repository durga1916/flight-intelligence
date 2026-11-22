import streamlit as st
import psycopg2
import pandas as pd
import plotly.express as px
from streamlit_autorefresh import st_autorefresh

st.set_page_config(page_title="Flight Operations Intelligence", layout="wide")
st_autorefresh(interval=60_000, key="refresh")   # re-run app every 60s -> live feel
st.title("✈️ Real-Time Flight Operations Intelligence — India")

@st.cache_data(ttl=55)
def load_data():
    conn = psycopg2.connect(st.secrets["DB_URL"], connect_timeout=15)
    try:
        df = pd.read_sql("SELECT * FROM flight_snapshots ORDER BY collected_at DESC", conn)
    finally:
        conn.close()
    df["collected_at"] = pd.to_datetime(df["collected_at"])
    return df

try:
    df = load_data()
except Exception as e:
    st.error(f"Database connection failed: {e}")
    st.stop()

latest = df[df["collected_at"] == df["collected_at"].max()]
fresh_sec = int((pd.Timestamp.now() - latest["collected_at"].max()).total_seconds())

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Aircraft (latest snapshot)", latest["icao24"].nunique())
c2.metric("Unique aircraft (all data)", df["icao24"].nunique())
c3.metric("Avg speed (m/s)", round(latest["velocity_ms"].mean(), 1))
c4.metric("Avg altitude (m)", round(latest["altitude_m"].replace(-1, None).mean(), 0))
c5.metric("Data age", f"{fresh_sec//60}m {fresh_sec%60}s" if fresh_sec < 3600 else f"{fresh_sec//3600}h+")

st.caption(f"Last DB snapshot: {latest['collected_at'].max()} | Total rows: {len(df):,}")

st.subheader("Live Aircraft Map (hover for details)")
airborne = latest[~latest["on_ground"]].dropna(subset=["latitude", "longitude"])
fig = px.scatter_map(
    airborne, lat="latitude", lon="longitude",
    color="altitude_m", size="velocity_ms",
    hover_name="callsign", hover_data={"origin_country": True, "altitude_m": True,
                                       "velocity_ms": True, "latitude": False, "longitude": False},
    color_continuous_scale="Turbo", zoom=3.5, height=550,
    labels={"altitude_m": "Altitude (m)", "velocity_ms": "Speed (m/s)"},
)
fig.update_layout(
    margin=dict(l=0, r=0, t=0, b=0),
    map=dict(center=dict(lat=21.0, lon=80.0), zoom=3.8),
)
st.plotly_chart(fig, use_container_width=True)

left, right = st.columns(2)
with left:
    st.subheader("Aircraft by Country (latest)")
    country = (latest.groupby("origin_country")["icao24"].nunique()
               .reset_index(name="Aircraft")
               .sort_values("Aircraft", ascending=False).head(15))
    st.dataframe(country, hide_index=True, use_container_width=True)

with right:
    st.subheader("Aircraft Activity Over Time")
    activity = (df.groupby("collected_at")["icao24"].nunique()
                .reset_index().rename(columns={"icao24": "Aircraft"}).tail(200))
    st.line_chart(activity.set_index("collected_at"))

st.subheader("Anomaly Alerts (latest snapshot)")
anomalies = latest[
    ((latest["altitude_m"] > 8000) & (latest["velocity_ms"] < 30)) |   # high & slow
    ((latest["altitude_m"] == -1) & (~latest["on_ground"]))            # unknown altitude, airborne
]
if anomalies.empty:
    st.success("No anomalies detected in the latest snapshot.")
else:
    st.warning(f"{len(anomalies)} potential anomalies detected.")
    st.dataframe(anomalies[["callsign", "origin_country", "altitude_m",
                            "velocity_ms", "latitude", "longitude"]],
                 hide_index=True, use_container_width=True)

with st.expander("Data Quality"):
    st.dataframe({
        "Total records": [f"{len(df):,}"],
        "Snapshots": [df["collected_at"].nunique()],
        "Unique aircraft": [df["icao24"].nunique()],
        "Missing coords": [int(df["latitude"].isna().sum())],
        "Missing callsign": [int(df["callsign"].isna().sum())],
        "Rows in last hour": [len(df[df["collected_at"] >= df["collected_at"].max() - pd.Timedelta(hours=1)])],
        "Latest snapshot": [str(df["collected_at"].max())],
    })
