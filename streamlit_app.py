#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""Streamlit 入口：部署到 https://share.streamlit.io （免费，需公开 GitHub 仓库）"""

from __future__ import annotations

import io

import streamlit as st

from weathercomcn_warning_dashboard import (
    china_now,
    crawl_all_provinces,
    render_dashboard,
    render_map_dashboard,
)

st.set_page_config(
    page_title="全国气象预警地图",
    page_icon="🌩️",
    layout="wide",
    initial_sidebar_state="collapsed",
)

st.markdown(
    """
<style>
    .block-container { padding-top: 1.2rem; padding-bottom: 2rem; }
    div[data-testid="stImage"] img { border-radius: 8px; }
    .save-tip { color: #666; font-size: 0.95rem; line-height: 1.6; }
</style>
""",
    unsafe_allow_html=True,
)


@st.cache_data(ttl=600, show_spinner=False)
def fetch_warning_data():
    return crawl_all_provinces()


def build_image(df, province_counts: dict, style: str) -> tuple[bytes, str]:
    buf = io.BytesIO()
    if style == "map":
        render_map_dashboard(df, province_counts, buf)
    else:
        render_dashboard(df, province_counts, buf)
    buf.seek(0)
    ts = china_now().strftime("%Y%m%d_%H%M")
    filename = f"全国各省预警一页图_{ts}.png"
    return buf.getvalue(), filename


st.title("全国各省气象灾害预警地图")
st.caption("数据源：中国天气网预警列表页 · 仅供个人学习参考")

col1, col2 = st.columns([1, 1])
with col1:
    style = st.radio("展示样式", ["map", "grid"], format_func=lambda x: "地图版" if x == "map" else "分省卡片", horizontal=True)
with col2:
    generate = st.button("生成 / 刷新", type="primary", use_container_width=True)

if "image_bytes" not in st.session_state:
    st.session_state.image_bytes = None
    st.session_state.image_name = ""
    st.session_state.generated_at = None
    st.session_state.alarm_count = 0

if generate:
    with st.spinner("正在抓取预警数据并绘制，约需 30–60 秒…"):
        try:
            df, province_counts = fetch_warning_data()
            image_bytes, image_name = build_image(df, province_counts, style)
            st.session_state.image_bytes = image_bytes
            st.session_state.image_name = image_name
            st.session_state.generated_at = china_now()
            st.session_state.alarm_count = len(df)
            st.session_state.style = style
        except RuntimeError as exc:
            st.error(f"抓取失败：{exc}")
        except Exception as exc:
            st.error(f"生成失败：{exc}")

if st.session_state.image_bytes:
    meta = st.session_state.generated_at.strftime("%Y-%m-%d %H:%M") if st.session_state.generated_at else ""
    st.success(f"已生成 · {meta} · 共 {st.session_state.alarm_count} 条预警")

    st.image(st.session_state.image_bytes, use_container_width=True)

    dl_col, tip_col = st.columns([1, 1])
    with dl_col:
        st.download_button(
            label="下载 PNG 图片",
            data=st.session_state.image_bytes,
            file_name=st.session_state.image_name,
            mime="image/png",
            type="primary",
            use_container_width=True,
        )
    with tip_col:
        st.markdown(
            '<p class="save-tip">📱 <b>保存到相册</b><br>'
            "iPhone：长按上方图片 →「存储到照片」<br>"
            "安卓：点「下载 PNG」后，在相册/下载里查看</p>",
            unsafe_allow_html=True,
        )
else:
    st.info("点击「生成 / 刷新」获取最新全国预警汇总图。")

with st.expander("关于"):
    st.markdown(
        """
- 本页由中国天气网预警**列表页**数据自动生成，非官方 API。
- 数据缓存约 10 分钟；点「生成 / 刷新」可强制更新。
- 部署在 [Streamlit Community Cloud](https://share.streamlit.io) 上，**完全免费**。
- 仅供个人学习、内部参考；公开发布或商用请联系中国天气网/中国气象局确认授权。
"""
    )
