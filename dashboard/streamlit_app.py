"""Separate Streamlit dashboard that consumes /dashboard/metrics and /cluster/status."""

import time

import requests
import streamlit as st

st.set_page_config(page_title="Recovery Agent Dashboard", layout="wide")
st.title("Razorpay Recovery Agent — Live Monitor")

API_BASE = "http://localhost:8000"

metrics_placeholder = st.empty()
cluster_placeholder = st.empty()

while True:
    # --- Metrics ---
    try:
        resp = requests.get(f"{API_BASE}/dashboard/metrics", timeout=2)
        metrics = resp.json()
        with metrics_placeholder.container():
            st.subheader("Recovery Metrics")
            col1, col2, col3 = st.columns(3)
            col1.metric("Agent Recovered", metrics["agent_recovered_count"])
            col2.metric("Human Recovered", metrics["human_recovered_count"])
            col3.metric("Control Recovered", metrics["control_recovered_count"])

            col4, col5, col6 = st.columns(3)
            col4.metric("Treatment Recovery Rate", f"{metrics['treatment_recovery_rate']*100:.1f}%")
            col5.metric("Control Recovery Rate", f"{metrics['control_recovery_rate']*100:.1f}%")
            col6.metric("Incremental Rate", f"{metrics['incremental_recovery_rate']*100:.1f}%")

            st.progress(min(metrics["compliance_score"] / 100.0, 1.0))
    except Exception as e:
        st.error(f"Metrics fetch failed: {e}")

    # --- Cluster status ---
    try:
        resp = requests.get(f"{API_BASE}/cluster/status", timeout=2)
        cluster = resp.json()
        with cluster_placeholder.container():
            st.subheader("etcd Cluster Leader")
            for node in cluster["nodes"]:
                name = node["name"]
                if name == cluster.get("leader"):
                    st.success(f"★ {name} (LEADER)")
                else:
                    st.text(name)
    except Exception as e:
        st.error(f"Cluster fetch failed: {e}")

    time.sleep(1)