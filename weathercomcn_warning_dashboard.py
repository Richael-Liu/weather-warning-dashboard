#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
中国天气网预警列表 → 全国各省“一张图”自动汇总

这个版本不使用心知天气 API。
它读取中国天气网预警列表页使用的数据接口：
https://product.weather.com.cn/alarm/grepalarm_cn.php
（页面 https://www.weather.com.cn/alarm/newalarmlist.shtml 通过 JS 动态加载该接口）

注意：
- 这不是中国天气网公开授权 API，而是网页列表页抓取。
- 适合个人学习、内部自动化参考。
- 如果要公开发布/商业使用，请联系中国天气网/中国气象局获取授权。

运行：
    pip install -r requirements.txt
    python weathercomcn_warning_dashboard.py

输出：
    output/weathercomcn_alarms_时间.csv
    output/全国各省预警一页图_时间.png   （默认地图样式，可用 --style grid 生成分省卡片）
"""

import re
import json
import time
import argparse
from io import BytesIO
from pathlib import Path
from datetime import datetime
from textwrap import wrap

import requests
import pandas as pd
import matplotlib.pyplot as plt
from matplotlib.path import Path as MplPath
from matplotlib.patches import Rectangle, FancyBboxPatch, PathPatch, ConnectionPatch
from matplotlib import font_manager
from matplotlib.font_manager import FontProperties

try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

# 不读取系统 HTTP 代理（如 Clash 127.0.0.1:7890），避免访问 weather.com.cn 时 SSL 超时
_HTTP_SESSION = requests.Session()
_HTTP_SESSION.trust_env = False

ALARM_LIST_API = "https://product.weather.com.cn/alarm/grepalarm_cn.php"
CHINA_GEOJSON_URL = "https://geo.datav.aliyun.com/areas_v3/bound/100000_full.json"
CHINA_GEOJSON_CACHE = Path(__file__).resolve().parent / "data" / "china_provinces.json"

# 部分省份图标锚点微调，避免落在省界外或与邻省重叠
ICON_ANCHOR_OVERRIDE = {
    "北京市": (116.15, 41.15),
    "天津市": (119.15, 38.75),
    "上海市": (121.85, 30.95),
    "重庆市": (107.70, 29.90),
    "河北省": (114.65, 38.15),
    "江苏省": (119.60, 33.20),
    "浙江省": (120.30, 29.40),
    "山东省": (117.85, 35.75),
    "河南省": (113.40, 34.20),
    "湖北省": (112.20, 31.20),
    "江西省": (115.40, 27.40),
    "福建省": (118.10, 26.30),
    "山西省": (110.85, 36.65),
    "广东省": (112.35, 25.35),
    "海南省": (109.80, 19.25),
    "台湾省": (120.95, 23.80),
    "香港特别行政区": (115.35, 23.05),
    "澳门特别行政区": (112.95, 21.55),
}

# 面积较小、标注易被邻省遮挡的省份：缩小字号并提高绘制优先级
PROVINCE_LABEL_SCALE = {
    "北京市": 0.85,
    "天津市": 0.78,
    "河北省": 0.80,
    "山西省": 0.80,
    "上海市": 0.85,
    "重庆市": 0.88,
    "香港特别行政区": 0.72,
    "澳门特别行政区": 0.68,
}
SMALL_PROVINCES = set(PROVINCE_LABEL_SCALE)
# 仅固定面积最小、不宜被算法推走的标注
PINNED_LABEL_PROVINCES = {
    "北京市", "天津市",
    "香港特别行政区", "澳门特别行政区",
}

# 地图旁单独展示的城市（area_id 为市级前缀，不与所在省地图标注重叠）
EXTRA_CITY_PANELS = {
    "合肥市": {
        "area_id": "1012201",
        "parent_province": "安徽省",
        "geo_adcode": "340100",
        "label": "合肥",
    },
}

CITY_GEOJSON_URL = "https://geo.datav.aliyun.com/areas_v3/bound/{adcode}.json"

GRADE_OBJ = {
    "01": "蓝色", "02": "黄色", "03": "橙色", "04": "红色", "05": "白色",
}
KIND_OBJ = {
    "01": "台风", "02": "暴雨", "03": "暴雪", "04": "寒潮", "05": "大风", "06": "沙尘暴",
    "07": "高温", "08": "干旱", "09": "雷电", "10": "冰雹", "11": "霜冻", "12": "大雾",
    "13": "霾", "14": "道路结冰", "51": "海上大雾", "52": "雷暴大风", "53": "持续低温",
    "54": "浓浮尘", "55": "龙卷风", "56": "低温冻害", "57": "海上大风", "58": "低温雨雪冰冻",
    "59": "强对流", "60": "臭氧", "61": "大雪", "62": "强降雨", "63": "强降温", "64": "雪灾",
    "65": "森林（草原）火险", "66": "雷暴", "67": "严寒", "68": "沙尘", "69": "海上雷雨大风",
    "70": "海上雷电", "71": "海上台风", "72": "低温", "91": "寒冷", "92": "灰霾",
    "93": "雷雨大风", "94": "森林火险", "95": "降温", "96": "道路冰雪", "97": "干热风",
    "98": "空气重污染", "99": "低温",
}


# -----------------------------
# 省级区域代码
# 说明：
# 10101-10131 为中国天气网常用省级/区域前缀。
# 港澳台在中国天气网页面中也有入口，但不同页面可能不走同一套列表接口；
# 本脚本默认稳定抓取大陆31个省级行政区，港澳台卡片保留为0。
# -----------------------------
AREA_IDS = {
    "北京市": "10101",
    "上海市": "10102",
    "天津市": "10103",
    "重庆市": "10104",
    "黑龙江省": "10105",
    "吉林省": "10106",
    "辽宁省": "10107",
    "内蒙古自治区": "10108",
    "河北省": "10109",
    "山西省": "10110",
    "陕西省": "10111",
    "山东省": "10112",
    "新疆维吾尔自治区": "10113",
    "西藏自治区": "10114",
    "青海省": "10115",
    "甘肃省": "10116",
    "宁夏回族自治区": "10117",
    "河南省": "10118",
    "江苏省": "10119",
    "湖北省": "10120",
    "浙江省": "10121",
    "安徽省": "10122",
    "福建省": "10123",
    "江西省": "10124",
    "湖南省": "10125",
    "贵州省": "10126",
    "四川省": "10127",
    "广东省": "10128",
    "云南省": "10129",
    "广西壮族自治区": "10130",
    "海南省": "10131",
}

PROVINCES_BY_REGION = {
    "华北": ["北京市", "天津市", "河北省", "山西省", "内蒙古自治区"],
    "东北": ["辽宁省", "吉林省", "黑龙江省"],
    "华东": ["上海市", "江苏省", "浙江省", "安徽省", "福建省", "江西省", "山东省", "台湾省"],
    "华中": ["河南省", "湖北省", "湖南省"],
    "华南": ["广东省", "广西壮族自治区", "海南省", "香港特别行政区", "澳门特别行政区"],
    "西南": ["重庆市", "四川省", "贵州省", "云南省", "西藏自治区"],
    "西北": ["陕西省", "甘肃省", "青海省", "宁夏回族自治区", "新疆维吾尔自治区"],
}

ALL_PROVINCES = [p for arr in PROVINCES_BY_REGION.values() for p in arr]

PROV_SHORT = {
    "北京市": "北京", "天津市": "天津", "河北省": "河北", "山西省": "山西",
    "内蒙古自治区": "内蒙古", "辽宁省": "辽宁", "吉林省": "吉林", "黑龙江省": "黑龙江",
    "上海市": "上海", "江苏省": "江苏", "浙江省": "浙江", "安徽省": "安徽",
    "福建省": "福建", "江西省": "江西", "山东省": "山东", "河南省": "河南",
    "湖北省": "湖北", "湖南省": "湖南", "广东省": "广东", "广西壮族自治区": "广西",
    "海南省": "海南", "重庆市": "重庆", "四川省": "四川", "贵州省": "贵州",
    "云南省": "云南", "西藏自治区": "西藏", "陕西省": "陕西", "甘肃省": "甘肃",
    "青海省": "青海", "宁夏回族自治区": "宁夏", "新疆维吾尔自治区": "新疆",
    "香港特别行政区": "香港", "澳门特别行政区": "澳门", "台湾省": "台湾",
    "合肥市": "合肥",
}

LEVEL_RANK = {"红色": 4, "橙色": 3, "黄色": 2, "蓝色": 1, "白色": 0, "未知": 0, "": 0}
RANK_LEVEL = {4: "红色", 3: "橙色", 2: "黄色", 1: "蓝色", 0: "无预警"}

LEVEL_COLORS = {
    "红色": "#D7191C",
    "橙色": "#F28E2B",
    "黄色": "#FFD22E",
    "蓝色": "#2B6CB0",
    "白色": "#E5E7EB",
    "未知": "#9CA3AF",
    "无预警": "#F4F1E8",
}
LEVEL_LIGHT = {
    "红色": "#FFE7E7",
    "橙色": "#FFF0DD",
    "黄色": "#FFF8C7",
    "蓝色": "#EAF2FF",
    "白色": "#F5F5F5",
    "未知": "#F2F2F2",
    "无预警": "#FAF8F0",
}
LEVEL_SHORT = {"红色": "红", "橙色": "橙", "黄色": "黄", "蓝色": "蓝", "白色": "白", "未知": "未"}

WARNING_TYPES = [
    "海上雷雨大风", "低温雨雪冰冻", "空气重污染", "森林（草原）火险",
    "道路冰雪", "道路结冰", "雷暴大风", "雷雨大风", "强降雨",
    "强对流", "强降温", "地质灾害", "山洪灾害", "中小河流洪水",
    "森林火险", "草原火险", "低温冻害", "海上大风", "海上大雾",
    "海上雷电", "海上台风", "持续低温", "浓浮尘",
    "暴雨", "暴雪", "寒潮", "大风", "沙尘暴", "高温", "干旱",
    "雷电", "冰雹", "霜冻", "大雾", "霾", "台风", "龙卷风",
    "臭氧", "大雪", "雪灾", "雷暴", "严寒", "沙尘", "低温",
    "寒冷", "灰霾", "降温", "冰冻", "干热风", "其它气象灾害", "其他气象灾害",
]
WARNING_TYPES = sorted(WARNING_TYPES, key=len, reverse=True)


def china_now():
    if ZoneInfo is not None:
        return datetime.now(ZoneInfo("Asia/Shanghai"))
    return datetime.now()


def _is_file_path(out_path) -> bool:
    return isinstance(out_path, (str, Path))


def setup_font():
    candidates = [
        "C:/Windows/Fonts/msyh.ttc",
        "C:/Windows/Fonts/msyh.ttf",
        "C:/Windows/Fonts/simhei.ttf",
        "C:/Windows/Fonts/simsun.ttc",
        "/System/Library/Fonts/PingFang.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    ]
    chosen_name = None
    for fp in candidates:
        if Path(fp).exists():
            try:
                font_manager.fontManager.addfont(fp)
                chosen_name = FontProperties(fname=fp).get_name()
                break
            except Exception:
                pass
    if chosen_name:
        plt.rcParams["font.sans-serif"] = [chosen_name, "Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
    else:
        plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "Noto Sans CJK SC", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False


def extract_type_level(title: str):
    title = str(title)
    level = "未知"
    for lev in ["红色", "橙色", "黄色", "蓝色", "白色"]:
        if lev in title:
            level = lev
            break

    # 优先用“发布xxx蓝色预警”的结构
    m = re.search(r"发布(.+?)(红色|橙色|黄色|蓝色|白色)预警", title)
    if m:
        typ = m.group(1).strip()
        typ = typ.replace("气象", "")
        return typ or "未知", level

    for typ in WARNING_TYPES:
        if typ in title:
            return typ, level

    return "未知", level


def clean_text(s: str) -> str:
    return re.sub(r"\s+", " ", str(s)).strip()


def _request_headers():
    return {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                      "(KHTML, like Gecko) Chrome/125 Safari/537.36",
        "Referer": "https://www.weather.com.cn/alarm/newalarmlist.shtml",
        "Accept-Language": "zh-CN,zh;q=0.9",
    }


def fetch_all_alarm_items(timeout=30):
    last_err = None
    for _ in range(2):
        try:
            r = _HTTP_SESSION.get(ALARM_LIST_API, headers=_request_headers(), timeout=timeout)
            r.raise_for_status()
            r.encoding = r.apparent_encoding or "utf-8"
            m = re.search(r"alarminfo\s*=\s*(\{.*\})", r.text, re.S)
            if not m:
                raise RuntimeError("响应中未找到 alarminfo 数据")
            info = json.loads(m.group(1))
            data = info.get("data") or []
            count = info.get("count", len(data))
            return data, int(count)
        except Exception as e:
            last_err = e
            time.sleep(1.2)
    raise RuntimeError(f"预警列表 API 获取失败：{last_err}")


def _parse_alarm_file_key(file_key: str):
    parts = str(file_key).split("-")
    area_code = parts[0] if parts else ""
    dt_raw = parts[1] if len(parts) > 1 else ""
    type_grade = parts[2].replace(".html", "") if len(parts) > 2 else ""
    pub_time = ""
    if len(dt_raw) >= 14 and dt_raw[:14].isdigit():
        pub_time = (
            f"{dt_raw[0:4]}-{dt_raw[4:6]}-{dt_raw[6:8]} "
            f"{dt_raw[8:10]}:{dt_raw[10:12]}:{dt_raw[12:14]}"
        )
    kind_code = type_grade[0:2] if len(type_grade) >= 2 else ""
    grade_code = type_grade[2:4] if len(type_grade) >= 4 else ""
    typ = KIND_OBJ.get(kind_code, "未知")
    level = GRADE_OBJ.get(grade_code, "未知")
    return area_code, pub_time, typ, level


def parse_alarm_item(item, province: str, area_id: str):
    file_key = item[1]
    area_code, pub_time, typ, level = _parse_alarm_file_key(file_key)
    location = clean_text(item[0]) if item else ""
    title = clean_text(item[6]) if len(item) > 6 and item[6] else f"{location}发布{typ}{level}预警"
    if "发布" not in title and location:
        title = f"{location}发布{typ}{level}预警"
    return {
        "province": province,
        "area_id": area_id,
        "area_code": area_code,
        "title": title,
        "type": typ,
        "level": level,
        "level_rank": LEVEL_RANK.get(level, 0),
        "pub_time": pub_time,
        "source": f"https://www.weather.com.cn/alarm/newalarmcontent.shtml?file={file_key}",
    }


def filter_items_for_area(items, area_id: str):
    prefix_len = 5 if len(area_id) <= 5 else 7 if len(area_id) <= 7 else 9
    prefix = area_id[:prefix_len]
    matched = []
    for item in items:
        code = str(item[1]).split("-")[0]
        if len(code) >= prefix_len and code[:prefix_len] == prefix:
            matched.append(item)
    return matched


def crawl_all_provinces(sleep_min=0.3, sleep_max=0.8):
    del sleep_min, sleep_max

    print("抓取：全国预警列表 API")
    try:
        all_items, total = fetch_all_alarm_items()
    except Exception as e:
        raise RuntimeError(
            "预警列表 API 获取失败，未获取到任何预警数据。"
            "请检查网络连接；若本机开启了代理（如 Clash 7890），请关闭代理或为 weather.com.cn 设置直连。"
            f" 详情：{e}"
        ) from e

    print(f"  全国合计 {total} 条")

    all_rows = []
    province_counts = {}
    for province, area_id in AREA_IDS.items():
        filtered = filter_items_for_area(all_items, area_id)
        rows = [parse_alarm_item(item, province, area_id) for item in filtered]
        all_rows.extend(rows)
        province_counts[province] = len(filtered)
        print(f"  {province}: {len(filtered)} 条")

    for city, cfg in EXTRA_CITY_PANELS.items():
        filtered = filter_items_for_area(all_items, cfg["area_id"])
        rows = [parse_alarm_item(item, city, cfg["area_id"]) for item in filtered]
        all_rows.extend(rows)
        province_counts[city] = len(filtered)
        print(f"  {city}: {len(filtered)} 条")

    df = pd.DataFrame(all_rows)
    if df.empty:
        df = pd.DataFrame(columns=[
            "province", "area_id", "area_code", "title", "type", "level",
            "level_rank", "pub_time", "source",
        ])

    df["listed_count"] = df["province"].map(province_counts)
    return df, province_counts


def summarize_province(df: pd.DataFrame, province: str, listed_count=None, max_icons=3):
    sub = df[df["province"] == province].copy() if not df.empty else pd.DataFrame()
    count = int(listed_count) if listed_count is not None and pd.notna(listed_count) else len(sub)

    if sub.empty and count <= 0:
        return {"count": 0, "max_level": "无预警", "active_levels": [], "tags": [], "icon_items": [], "summary": "暂无预警"}

    if sub.empty and count > 0:
        return {"count": count, "max_level": "未知", "active_levels": [], "tags": [], "icon_items": [], "summary": "有预警"}

    sub["level_rank"] = sub["level"].map(LEVEL_RANK).fillna(0).astype(int)
    max_rank = int(sub["level_rank"].max())
    max_level = RANK_LEVEL.get(max_rank, "未知")

    level_order = ["红色", "橙色", "黄色", "蓝色"]
    active_levels = [lev for lev in level_order if lev in set(sub["level"])]

    type_best = {}
    for _, r in sub.iterrows():
        typ = str(r.get("type", "")).strip() or "未知"
        rank = int(r.get("level_rank", 0))
        level = str(r.get("level", "")).strip()
        prev = type_best.get(typ)
        if prev is None or rank > prev[0]:
            type_best[typ] = (rank, level)

    tags = []
    icon_items = []
    for typ, (_, level) in sorted(type_best.items(), key=lambda x: (-x[1][0], x[0]))[:max_icons]:
        typ_short = typ.replace("其它气象灾害", "其它").replace("其他气象灾害", "其它")
        if len(typ_short) > 4:
            typ_short = typ_short[:4]
        lev_short = LEVEL_SHORT.get(level, "未")
        tags.append(f"{typ_short}·{lev_short}")
        icon_items.append({"label": typ_short, "level": level})

    if len(type_best) > max_icons:
        tags.append("…")

    summary = "  ".join(tags) if tags else "有预警"
    return {
        "count": count if count is not None else len(sub),
        "max_level": max_level,
        "active_levels": active_levels,
        "tags": tags,
        "icon_items": icon_items,
        "summary": summary,
    }


def _prov_display(province: str) -> str:
    return PROV_SHORT.get(province, province[:2] if len(province) >= 2 else province)


def _type_label(typ) -> str:
    typ = str(typ).strip() or "未知"
    return typ.replace("其它气象灾害", "其它").replace("其他气象灾害", "其它")


def _wrap_cn_lines(text: str, width: int = 24):
    lines = []
    for para in str(text).split("\n"):
        if not para:
            lines.append("")
        else:
            lines.extend(wrap(para, width=width) or [""])
    return lines


def _format_key_province_line(prov: str, sub: pd.DataFrame) -> str:
    short = _prov_display(prov)
    clauses = []

    red = sub[sub["level"] == "红色"]
    if not red.empty:
        for typ in red["type"].value_counts().index:
            n = int((red["type"] == typ).sum())
            t = _type_label(typ)
            clauses.append(f"{t}红色预警{n}条" if n > 1 else f"{t}红色预警")

    orange = sub[sub["level"] == "橙色"]
    if not orange.empty and len(clauses) < 2:
        types = orange["type"].value_counts().index.tolist()
        ts = "、".join(_type_label(t) for t in types[:4])
        if len(types) > 4:
            ts += "等"
        if len(types) >= 2:
            clauses.append(f"{ts}橙色预警叠加")
        else:
            clauses.append(f"{ts}橙色预警")

    if len(clauses) < 2:
        for lev in ["黄色", "蓝色"]:
            lev_sub = sub[sub["level"] == lev]
            if lev_sub.empty:
                continue
            types = lev_sub["type"].value_counts().index.tolist()[:3]
            ts = "、".join(_type_label(t) for t in types)
            if len(lev_sub["type"].unique()) > 3:
                ts += "等"
            if len(types) >= 2:
                clauses.append(f"{ts}{lev}预警较集中")
            else:
                clauses.append(f"{ts}{lev}预警")
            if len(clauses) >= 2:
                break

    if not clauses:
        return f"{short}：预警{len(sub)}条。"
    return f"{short}：{'；'.join(clauses[:2])}。"


def _province_highlight_score(sub: pd.DataFrame) -> float:
    if sub.empty:
        return 0.0
    score = 0.0
    score += (sub["level"] == "红色").sum() * 100
    score += (sub["level"] == "橙色").sum() * 25
    score += (sub["level"] == "黄色").sum() * 4
    score += (sub["level"] == "蓝色").sum() * 1
    score += sub["type"].nunique() * 8
    score += len(sub) * 0.5
    return score


def build_key_warning_summary(df: pd.DataFrame) -> dict:
    """从预警明细自动生成重点预警摘要（高等级 / 主要灾种 / 重点省份）。"""
    title = "重点预警摘要"
    if df is None or df.empty:
        return {
            "title": title,
            "sections": [
                {"heading": "高等级预警", "raw": "当前暂无生效预警。"},
                {"heading": "主要灾种", "raw": "暂无数据。"},
                {"heading": "重点省份", "raw": "暂无。"},
            ],
        }

    work = df[df["province"].isin(ALL_PROVINCES)].copy()
    if work.empty:
        return build_key_warning_summary(pd.DataFrame())

    work["type"] = work["type"].fillna("未知").astype(str).str.strip()
    work["level"] = work["level"].fillna("未知").astype(str).str.strip()

    red = work[work["level"] == "红色"]
    red_total = len(red)
    if red_total == 0:
        red_line = "红色预警：无。"
    else:
        groups = red.groupby(["province", "type"]).size().reset_index(name="n")
        groups = groups.sort_values(["province", "type"])
        parts = [
            f"{_prov_display(r.province)}{_type_label(r.type)}{int(r.n)}条"
            for _, r in groups.iterrows()
        ]
        red_line = f"红色预警：{'、'.join(parts)}，共{red_total}条。"

    orange = work[work["level"] == "橙色"]
    orange_total = len(orange)
    if orange_total == 0:
        orange_line = "橙色预警：无。"
    elif orange_total <= 10:
        groups = orange.groupby(["province", "type"]).size().reset_index(name="n")
        groups = groups.sort_values(["n", "province"], ascending=[False, True])
        parts = [
            f"{_prov_display(r.province)}{_type_label(r.type)}{int(r.n)}条"
            for _, r in groups.iterrows()
        ]
        orange_line = f"橙色预警：{'、'.join(parts)}，共{orange_total}条。"
    else:
        prov_n = orange.groupby("province").size().sort_values(ascending=False)
        top_names = "、".join(_prov_display(p) for p in prov_n.head(6).index)
        if len(prov_n) > 6:
            top_names += "等地"
        type_names = "、".join(_type_label(t) for t in orange["type"].value_counts().head(4).index)
        orange_line = f"橙色预警：{top_names}较多，主要涉及{type_names}等，共{orange_total}条。"

    type_total = work["type"].value_counts()
    top_types = "、".join(_type_label(t) for t in type_total.head(4).index)
    yellow_top = work[work["level"] == "黄色"]["type"].value_counts().head(3).index.tolist()
    blue_top = work[work["level"] == "蓝色"]["type"].value_counts().head(3).index.tolist()
    main_line = f"{top_types}为当前主要预警类型"
    if yellow_top:
        main_line += f"；其中黄色预警以{'、'.join(_type_label(t) for t in yellow_top)}为主"
    if blue_top:
        main_line += f"，蓝色预警以{'、'.join(_type_label(t) for t in blue_top)}为主"
    main_line += "。"

    ranked = []
    for prov in ALL_PROVINCES:
        sub = work[work["province"] == prov]
        sc = _province_highlight_score(sub)
        if sc > 0:
            ranked.append((prov, sc, sub))
    ranked.sort(key=lambda x: (-x[1], -len(x[2])))
    pick_n = min(6, max(3, len(ranked))) if ranked else 0
    prov_lines = [_format_key_province_line(p, s) for p, _, s in ranked[:pick_n]]
    if not prov_lines:
        prov_lines = ["当前无重点省份。"]

    return {
        "title": title,
        "sections": [
            {"heading": "高等级预警", "raw": f"{red_line}\n{orange_line}"},
            {"heading": "主要灾种", "raw": main_line},
            {"heading": "重点省份", "raw": "\n".join(prov_lines)},
        ],
    }


def _draw_key_summary_panel(fig, summary: dict, content_left: float, content_width: float):
    """在图下方绘制三栏摘要，宽度与上方地图内容区对齐。"""
    col_x = [0.024, 0.350, 0.676]
    col_wrap = [22, 22, 21]

    col_lines = [
        _wrap_cn_lines(sec["raw"], width=w)
        for sec, w in zip(summary["sections"], col_wrap)
    ]
    max_body_lines = max(max(len(lines), 1) for lines in col_lines)
    panel_x = content_left + 0.004
    panel_w = content_width - 0.008
    panel_h = min(0.24, max(0.158, 0.088 + max_body_lines * 0.030))
    panel_y = 0.016

    ax_box = fig.add_axes([panel_x, panel_y, panel_w, panel_h], zorder=15)
    ax_box.set_facecolor("none")
    ax_box.axis("off")
    ax_box.add_patch(FancyBboxPatch(
        (0.008, 0.04), 0.984, 0.92,
        transform=ax_box.transAxes,
        boxstyle="round,pad=0.010,rounding_size=0.022",
        facecolor="#FFFFFF",
        edgecolor="#7A9BB8",
        linewidth=1.2,
        alpha=0.95,
        zorder=0,
    ))

    ax_box.text(
        0.018, 0.94, summary["title"],
        transform=ax_box.transAxes, ha="left", va="top",
        fontsize=11.5, fontweight="bold", color="#1A1A1A", zorder=2,
    )

    heading_y = 0.82
    body_top = 0.73
    body_bottom = 0.10
    line_step = (body_top - body_bottom) / max(max_body_lines, 1)

    for idx, (sec, cx, lines) in enumerate(
        zip(summary["sections"], col_x, col_lines)
    ):
        ax_box.text(
            cx, heading_y, f"{sec['heading']}：",
            transform=ax_box.transAxes, ha="left", va="top",
            fontsize=9.6, fontweight="bold", color="#2A2A2A", zorder=2,
        )

        y = body_top
        for line in lines:
            if not line:
                continue
            ax_box.text(
                cx, y, line,
                transform=ax_box.transAxes, ha="left", va="top",
                fontsize=8.8, color="#444444", zorder=2,
            )
            y -= line_step

        if idx > 0:
            ax_box.plot(
                [cx - 0.014, cx - 0.014], [body_bottom, heading_y + 0.03],
                transform=ax_box.transAxes,
                color="#C5D6E4", linewidth=0.9, zorder=1,
            )


def _draw_level_bar(ax, x, y, card_h, active_levels):
    bar_w = 12
    if not active_levels:
        ax.add_patch(Rectangle((x, y), bar_w, card_h, facecolor="#D8D8D8", edgecolor="none"))
        return
    seg_h = card_h / len(active_levels)
    for i, lev in enumerate(active_levels):
        ax.add_patch(Rectangle(
            (x, y + card_h - (i + 1) * seg_h), bar_w, seg_h,
            facecolor=LEVEL_COLORS[lev], edgecolor="none",
        ))


def load_china_geojson():
    CHINA_GEOJSON_CACHE.parent.mkdir(parents=True, exist_ok=True)
    if not CHINA_GEOJSON_CACHE.exists():
        r = _HTTP_SESSION.get(CHINA_GEOJSON_URL, timeout=60)
        r.raise_for_status()
        CHINA_GEOJSON_CACHE.write_bytes(r.content)
    return json.loads(CHINA_GEOJSON_CACHE.read_text(encoding="utf-8"))


def load_city_geojson(adcode: str):
    cache = CHINA_GEOJSON_CACHE.parent / f"city_{adcode}.json"
    if not cache.exists():
        url = CITY_GEOJSON_URL.format(adcode=adcode)
        r = _HTTP_SESSION.get(url, timeout=60)
        r.raise_for_status()
        cache.write_bytes(r.content)
    return json.loads(cache.read_text(encoding="utf-8"))


def _geometry_bounds(geometry):
    lons, lats = [], []
    for ring in _geometry_rings(geometry):
        for lon, lat in ring:
            lons.append(lon)
            lats.append(lat)
    if not lons:
        return 0, 1, 0, 1
    pad_lon = (max(lons) - min(lons)) * 0.18 or 0.08
    pad_lat = (max(lats) - min(lats)) * 0.22 or 0.08
    return (
        min(lons) - pad_lon, max(lons) + pad_lon,
        min(lats) - pad_lat, max(lats) + pad_lat,
    )


def _geometry_rings(geometry):
    geom_type = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if geom_type == "Polygon":
        return coords
    if geom_type == "MultiPolygon":
        rings = []
        for poly in coords:
            rings.extend(poly)
        return rings
    return []


def _draw_geometry(ax, geometry, facecolor, edgecolor, linewidth, zorder):
    for ring in _geometry_rings(geometry):
        if len(ring) < 3:
            continue
        codes = [MplPath.MOVETO] + [MplPath.LINETO] * (len(ring) - 2) + [MplPath.CLOSEPOLY]
        patch = PathPatch(
            MplPath(ring, codes),
            facecolor=facecolor,
            edgecolor=edgecolor,
            linewidth=linewidth,
            zorder=zorder,
        )
        ax.add_patch(patch)


def _province_anchor(props):
    name = props.get("name") or ""
    if name in ICON_ANCHOR_OVERRIDE:
        lon, lat = ICON_ANCHOR_OVERRIDE[name]
        return name, lon, lat
    point = props.get("centroid") or props.get("center")
    if not point:
        return name, None, None
    return name, float(point[0]), float(point[1])


def _icon_box_size(label, scale=1.0):
    n = max(len(label), 2)
    w = (0.62 * n + 0.72) * scale
    h = 0.58 * scale
    base_fs = 7.5 if n <= 3 else 7
    fontsize = max(5.5, base_fs * scale)
    return w, h, fontsize


def _label_bbox(lon, lat, icon_items, scale=1.0):
    """估算省名 + 预警图标的经纬度包围盒（含 matplotlib 文字白底留白）。"""
    name_y = lat + 0.62 * scale
    name_w = 2.05 * scale
    name_h = 1.05 * scale
    lon_min = lon - name_w / 2
    lon_max = lon + name_w / 2
    lat_max = name_y + name_h / 2
    lat_min = name_y - name_h / 2

    if icon_items:
        gap = 0.18 * scale
        y_cursor = lat + 0.08 * scale
        icon_w_max = name_w
        for item in icon_items[:3]:
            w, h, _ = _icon_box_size(item["label"], scale)
            icon_w_max = max(icon_w_max, w + 0.18 * scale)
            h = h + 0.12 * scale
            y_cursor -= h / 2
            lat_min = min(lat_min, y_cursor - h / 2)
            y_cursor -= h / 2 + gap
        lon_min = lon - icon_w_max / 2
        lon_max = lon + icon_w_max / 2

    margin = 0.18 * scale
    return lon_min - margin, lon_max + margin, lat_min - margin, lat_max + margin


def _find_label_overlaps(entries, gap=0.0):
    pairs = []
    for i, ea in enumerate(entries):
        for eb in entries[i + 1:]:
            ba = _label_bbox(ea["lon"], ea["lat"], ea["icon_items"], ea["scale"])
            bb = _label_bbox(eb["lon"], eb["lat"], eb["icon_items"], eb["scale"])
            if _bboxes_overlap(ba, bb, gap):
                pairs.append((ea, eb))
    return pairs


def _label_mobility(entry):
    if entry["name"] in PINNED_LABEL_PROVINCES:
        return 0.0
    return _label_move_weight(entry["name"], entry["icon_items"])


def _separate_label_pair(ea, eb, gap=0.08):
    ba = _label_bbox(ea["lon"], ea["lat"], ea["icon_items"], ea["scale"])
    bb = _label_bbox(eb["lon"], eb["lat"], eb["icon_items"], eb["scale"])
    if not _bboxes_overlap(ba, bb, gap):
        return False

    ma, mb = _label_mobility(ea), _label_mobility(eb)
    if ma == 0.0 and mb == 0.0:
        ma, mb = 0.5, 0.5

    overlap_lon = min(ba[1], bb[1]) - max(ba[0], bb[0]) + gap
    overlap_lat = min(ba[3], bb[3]) - max(ba[2], bb[2]) + gap
    cx_a = (ba[0] + ba[1]) / 2
    cy_a = (ba[2] + ba[3]) / 2
    cx_b = (bb[0] + bb[1]) / 2
    cy_b = (bb[2] + bb[3]) / 2

    if overlap_lon > 0 and overlap_lat > 0:
        if overlap_lon <= overlap_lat:
            shift, dx, dy = overlap_lon, (1.0 if cx_a >= cx_b else -1.0), 0.0
        else:
            shift, dx, dy = overlap_lat, 0.0, (1.0 if cy_a >= cy_b else -1.0)
    elif overlap_lon > 0:
        shift, dx, dy = overlap_lon, (1.0 if cx_a >= cx_b else -1.0), 0.0
    else:
        shift, dx, dy = overlap_lat, 0.0, (1.0 if cy_a >= cy_b else -1.0)

    total = ma + mb
    if ma == 0.0:
        eb["lon"] -= dx * shift
        eb["lat"] -= dy * shift
    elif mb == 0.0:
        ea["lon"] += dx * shift
        ea["lat"] += dy * shift
    else:
        ea["lon"] += dx * shift * (mb / total)
        ea["lat"] += dy * shift * (mb / total)
        eb["lon"] -= dx * shift * (ma / total)
        eb["lat"] -= dy * shift * (ma / total)
    return True


def _enforce_non_overlapping_labels(entries, max_passes=300, gap=0.08):
    """强制分离直至无重叠（或达到最大迭代次数）。"""
    for _ in range(max_passes):
        moved = False
        for ea, eb in _find_label_overlaps(entries, gap):
            if _separate_label_pair(ea, eb, gap):
                moved = True
        if not moved:
            break
    return entries


def _bboxes_overlap(a, b, pad=0.0):
    return not (a[1] + pad < b[0] or b[1] + pad < a[0] or a[3] + pad < b[2] or b[3] + pad < a[2])


def _label_move_weight(name, icon_items):
    """权重越小越容易被推移；小省份权重低，碰撞时尽量让大省让位。"""
    if name in SMALL_PROVINCES:
        return 0.35
    return 1.0 + len(icon_items or []) * 0.12


def _resolve_province_label_positions(entries, passes=60, pad=0.14):
    """迭代微调各省标注锚点，减少标注重叠。"""
    for entry in entries:
        entry["scale"] = PROVINCE_LABEL_SCALE.get(entry["name"], 1.0)
        entry["_origin"] = (entry["lon"], entry["lat"])

    for _ in range(passes):
        moved = False
        for i, ea in enumerate(entries):
            for eb in entries[i + 1:]:
                ba = _label_bbox(ea["lon"], ea["lat"], ea["icon_items"], ea["scale"])
                bb = _label_bbox(eb["lon"], eb["lat"], eb["icon_items"], eb["scale"])
                if not _bboxes_overlap(ba, bb, pad):
                    continue

                wa = _label_move_weight(ea["name"], ea["icon_items"])
                wb = _label_move_weight(eb["name"], eb["icon_items"])
                if ea["name"] in PINNED_LABEL_PROVINCES:
                    wa = 0.0
                if eb["name"] in PINNED_LABEL_PROVINCES:
                    wb = 0.0
                if wa == 0.0 and wb == 0.0:
                    continue
                total = wa + wb

                overlap_lon = min(ba[1], bb[1]) - max(ba[0], bb[0]) + pad
                overlap_lat = min(ba[3], bb[3]) - max(ba[2], bb[2]) + pad
                cx_a = (ba[0] + ba[1]) / 2
                cy_a = (ba[2] + ba[3]) / 2
                cx_b = (bb[0] + bb[1]) / 2
                cy_b = (bb[2] + bb[3]) / 2
                dx = cx_a - cx_b
                dy = cy_a - cy_b
                if abs(dx) < 1e-6 and abs(dy) < 1e-6:
                    dx, dy = 0.12, 0.08
                norm = (dx * dx + dy * dy) ** 0.5
                dx /= norm
                dy /= norm
                move = max(overlap_lon, overlap_lat, 0.06) * 0.55

                if wa == 0.0:
                    eb["lon"] -= dx * move
                    eb["lat"] -= dy * move
                elif wb == 0.0:
                    ea["lon"] += dx * move
                    ea["lat"] += dy * move
                else:
                    ea["lon"] += dx * move * (wb / total)
                    ea["lat"] += dy * move * (wb / total)
                    eb["lon"] -= dx * move * (wa / total)
                    eb["lat"] -= dy * move * (wa / total)
                moved = True
        if not moved:
            break

    for entry in entries:
        if entry["name"] in PINNED_LABEL_PROVINCES:
            entry["lon"], entry["lat"] = entry["_origin"]
        del entry["_origin"]

    entries = _enforce_non_overlapping_labels(entries, gap=0.10)
    if _find_label_overlaps(entries, gap=0.0):
        entries = _enforce_non_overlapping_labels(entries, max_passes=500, gap=0.14)
    if _find_label_overlaps(entries, gap=0.10):
        entries = _enforce_non_overlapping_labels(entries, max_passes=500, gap=0.16)
    return entries


def _draw_warning_icon(ax, x, y, label, level, w, h, fontsize):
    color = LEVEL_COLORS.get(level, "#999999")
    text_color = "white" if level in ("红色", "橙色", "蓝色") else "#222222"
    ax.add_patch(FancyBboxPatch(
        (x - w / 2, y - h / 2), w, h,
        boxstyle="round,pad=0.02,rounding_size=0.08",
        facecolor=color,
        edgecolor="#555555",
        linewidth=0.6,
        zorder=20,
    ))
    ax.text(
        x, y, label,
        ha="center", va="center",
        fontsize=fontsize, fontweight="bold",
        color=text_color,
        zorder=21,
    )


def _figure_y_for_lat(ax, lat):
    y0, y1 = ax.get_ylim()
    rel = (lat - y0) / (y1 - y0)
    pos = ax.get_position()
    return pos.y0 + rel * pos.height


def _figure_x_for_lon(ax, lon):
    x0, x1 = ax.get_xlim()
    rel = (lon - x0) / (x1 - x0)
    pos = ax.get_position()
    return pos.x0 + rel * pos.width


def _draw_province_on_map(ax, name, lon, lat, icon_items, scale=1.0, icon_scale=None, zorder_base=8):
    if icon_scale is None:
        icon_scale = scale
    short = PROV_SHORT.get(name, name[:2] if len(name) >= 2 else name)
    ax.text(
        lon, lat + 0.62 * scale, short,
        ha="center", va="center",
        fontsize=max(7, 9 * scale), fontweight="bold", color="#444444", zorder=zorder_base,
        bbox=dict(
            boxstyle="round,pad=0.22",
            facecolor="white",
            edgecolor="#BBBBBB",
            linewidth=0.5,
            alpha=0.92,
        ),
    )

    if not icon_items:
        return

    gap = 0.18 * icon_scale
    y_cursor = lat + 0.08 * scale
    for item in icon_items:
        w, h, fs = _icon_box_size(item["label"], icon_scale)
        y_cursor -= h / 2
        _draw_warning_icon(ax, lon, y_cursor, item["label"], item["level"], w, h, fs)
        y_cursor -= h / 2 + gap


def _draw_city_mini_map(ax, city_name, geo, info, parent_province=""):
    """在侧栏绘制市级轮廓，标注方式与各省一致。"""
    ax.set_facecolor("#D8ECF8")
    ax.axis("off")

    ax.text(
        0.5, 0.97, "市级单列报道",
        transform=ax.transAxes, ha="center", va="top",
        fontsize=8.5, fontweight="bold", color="#555555", zorder=12, clip_on=False,
    )

    ax.add_patch(FancyBboxPatch(
        (0.02, 0.02), 0.96, 0.96,
        transform=ax.transAxes,
        boxstyle="round,pad=0.012,rounding_size=0.02",
        facecolor="none",
        edgecolor="#7A9BB8",
        linestyle="--",
        linewidth=1.2,
        zorder=11,
        clip_on=False,
    ))

    features = geo.get("features") or []
    if not features:
        ax.text(0.5, 0.5, "暂无边界", transform=ax.transAxes, ha="center", va="center")
        return

    lon_min, lon_max, lat_min, lat_max = None, None, None, None
    for feature in features:
        geom = feature.get("geometry")
        if not geom:
            continue
        _draw_geometry(ax, geom, facecolor="#FAF7F0", edgecolor="#7EB6D7", linewidth=0.7, zorder=1)
        b = _geometry_bounds(geom)
        lon_min = b[0] if lon_min is None else min(lon_min, b[0])
        lon_max = b[1] if lon_max is None else max(lon_max, b[1])
        lat_min = b[2] if lat_min is None else min(lat_min, b[2])
        lat_max = b[3] if lat_max is None else max(lat_max, b[3])

    if lon_min is None:
        return

    lat_span = lat_max - lat_min
    lat_max += lat_span * 0.42
    lat_min -= lat_span * 0.06

    ax.set_xlim(lon_min, lon_max)
    ax.set_ylim(lat_min, lat_max)
    ax.set_aspect(1.05)

    span = max(lon_max - lon_min, lat_max - lat_min)
    label_scale = span / 3.0
    icon_scale = span / 7.5

    props = features[0].get("properties") or {}
    _, lon, lat = _province_anchor(props)
    if lon is None:
        lon = (lon_min + lon_max) / 2
        lat = (lat_min + lat_max) / 2

    icon_items = info.get("icon_items", [])
    if icon_items:
        capped = []
        for item in icon_items[:3]:
            w, h, fs = _icon_box_size(item["label"], icon_scale)
            max_w = span * 0.82
            if w > max_w:
                fs = max(5.5, fs * (max_w / w))
                w = max_w
            capped.append({**item, "_w": w, "_h": h, "_fs": fs})
        short = PROV_SHORT.get(city_name, city_name[:2])
        ax.text(
            lon, lat + 0.62 * label_scale, short,
            ha="center", va="center",
            fontsize=max(7, 9 * label_scale), fontweight="bold", color="#444444", zorder=8,
            bbox=dict(
                boxstyle="round,pad=0.22",
                facecolor="white",
                edgecolor="#BBBBBB",
                linewidth=0.5,
                alpha=0.92,
            ),
        )
        gap = 0.18 * icon_scale
        y_cursor = lat + 0.08 * label_scale
        for item in capped:
            w, h, fs = item["_w"], item["_h"], item["_fs"]
            y_cursor -= h / 2
            _draw_warning_icon(ax, lon, y_cursor, item["label"], item["level"], w, h, fs)
            y_cursor -= h / 2 + gap
    else:
        short = PROV_SHORT.get(city_name, city_name[:2])
        ax.text(
            lon, lat + 0.62 * label_scale, short,
            ha="center", va="center",
            fontsize=max(7, 9 * label_scale), fontweight="bold", color="#444444", zorder=8,
            bbox=dict(
                boxstyle="round,pad=0.22",
                facecolor="white",
                edgecolor="#BBBBBB",
                linewidth=0.5,
                alpha=0.92,
            ),
        )
        ax.text(
            lon, lat - 0.15 * label_scale, "暂无预警",
            ha="center", va="center",
            fontsize=max(7, 9 * label_scale), color="#999999", zorder=8,
        )


def _draw_city_inset_callout(fig, ax, ax_panel, parent_lon, parent_lat):
    """虚线引线：从所属省指向市级单列框。"""
    connector = ConnectionPatch(
        xyA=(parent_lon + 1.2, parent_lat),
        coordsA=ax.transData,
        xyB=(0.02, 0.45),
        coordsB=ax_panel.transAxes,
        arrowstyle="-|>",
        linestyle=(0, (4, 3)),
        color="#7A9BB8",
        linewidth=1.1,
        mutation_scale=9,
        zorder=3,
        clip_on=False,
    )
    fig.add_artist(connector)


def render_map_dashboard(df: pd.DataFrame, province_counts: dict, out_path: Path):
    setup_font()
    geo = load_china_geojson()

    fig = plt.figure(figsize=(12.0, 12), dpi=160)

    map_fw, map_fh = 0.74, 0.85
    city_fw, city_fh = 0.11, 0.15
    inset_gap = 0.010
    content_fw = map_fw + inset_gap + city_fw
    content_left = (1.0 - content_fw) / 2

    ax = fig.add_axes([content_left, 0.105, map_fw, map_fh])
    ax.set_facecolor("#D8ECF8")
    ax.set_xlim(72, 136)
    ax.set_ylim(16, 54)
    ax.set_aspect(1.05)
    ax.axis("off")

    province_info = {}
    for prov in ALL_PROVINCES:
        province_info[prov] = summarize_province(df, prov, province_counts.get(prov, 0))

    anhui_lat = 31.85
    anhui_lon = 117.22
    label_entries = []
    for feature in geo.get("features", []):
        props = feature.get("properties") or {}
        name = props.get("name")
        if not name:
            continue
        _draw_geometry(
            ax, feature.get("geometry"),
            facecolor="#FAF7F0",
            edgecolor="#7EB6D7",
            linewidth=0.7,
            zorder=1,
        )
        _, lon, lat = _province_anchor(props)
        if lon is None:
            continue
        if name == "安徽省":
            anhui_lat = lat
            anhui_lon = lon
        info = province_info.get(name, {"icon_items": []})
        label_entries.append({
            "name": name,
            "lon": lon,
            "lat": lat,
            "icon_items": info.get("icon_items", []),
        })

    label_entries = _resolve_province_label_positions(label_entries)
    label_entries.sort(key=lambda e: _label_move_weight(e["name"], e["icon_items"]), reverse=True)
    for entry in label_entries:
        zorder_base = 14 if entry["name"] in SMALL_PROVINCES else 8
        _draw_province_on_map(
            ax, entry["name"], entry["lon"], entry["lat"], entry["icon_items"],
            scale=entry["scale"], zorder_base=zorder_base,
        )

    panel_w, panel_h = city_fw, city_fh
    ax_pos = ax.get_position()
    panel_x = ax_pos.x1 - panel_w - 0.018
    panel_y = _figure_y_for_lat(ax, anhui_lat) - panel_h / 2
    panel_y = max(0.08, min(panel_y, 0.90 - panel_h))
    ax_panel = fig.add_axes([panel_x, panel_y, panel_w, panel_h])
    ax_panel.set_zorder(5)
    for city, cfg in EXTRA_CITY_PANELS.items():
        city_info = summarize_province(
            df, city, province_counts.get(city, 0), max_icons=3,
        )
        adcode = cfg.get("geo_adcode")
        if adcode:
            city_geo = load_city_geojson(adcode)
            _draw_city_mini_map(
                ax_panel, city, city_geo, city_info,
                parent_province=cfg.get("parent_province", ""),
            )
            _draw_city_inset_callout(fig, ax, ax_panel, anhui_lon, anhui_lat)

    summary = build_key_warning_summary(df)
    _draw_key_summary_panel(fig, summary, content_left, content_fw)

    level_counts = {lev: int((df["level"] == lev).sum()) if not df.empty else 0 for lev in ["红色", "橙色", "黄色", "蓝色"]}
    now_text = china_now().strftime("%Y-%m-%d %H:%M")
    fig.text(0.5, 0.97, f"全国各省气象灾害预警地图｜{now_text}",
             ha="center", va="top", fontsize=22, fontweight="bold", color="#111111")
    fig.text(
        0.5, 0.935,
        f"数据源：中国天气网｜红 {level_counts['红色']}｜橙 {level_counts['橙色']}｜黄 {level_counts['黄色']}｜蓝 {level_counts['蓝色']}",
        ha="center", va="top", fontsize=13, color="#444444",
    )

    if _is_file_path(out_path):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    save_kw = dict(bbox_inches="tight", pad_inches=0.08, facecolor="#EAF3FA")
    if isinstance(out_path, BytesIO):
        save_kw["format"] = "png"
    plt.savefig(out_path, **save_kw)
    plt.close(fig)


def render_dashboard(df: pd.DataFrame, province_counts: dict, out_path: Path):
    setup_font()

    W, H = 2400, 1450
    dpi = 160
    fig = plt.figure(figsize=(W / dpi, H / dpi), dpi=dpi)
    ax = plt.axes([0, 0, 1, 1])
    ax.set_xlim(0, W)
    ax.set_ylim(0, H)
    ax.axis("off")

    ax.add_patch(Rectangle((0, 0), W, H, facecolor="#FCFBF7", edgecolor="none"))

    total = 0
    for p in AREA_IDS:
        v = province_counts.get(p, 0)
        if isinstance(v, int):
            total += v
    level_counts = {lev: int((df["level"] == lev).sum()) if not df.empty else 0 for lev in ["红色", "橙色", "黄色", "蓝色"]}

    now_text = china_now().strftime("%Y-%m-%d %H:%M")
    title = f"全国各省气象灾害预警一页图｜{now_text}"
    subtitle = f"数据源：中国天气网预警列表页｜列表合计 {total} 条｜红 {level_counts['红色']}｜橙 {level_counts['橙色']}｜黄 {level_counts['黄色']}｜蓝 {level_counts['蓝色']}"

    ax.text(W / 2, H - 55, title, ha="center", va="center", fontsize=34, fontweight="bold", color="#111111")
    ax.text(W / 2, H - 102, subtitle, ha="center", va="center", fontsize=21, color="#333333")

    legend_x = 72
    legend_y = H - 128
    for i, lev in enumerate(["红色", "橙色", "黄色", "蓝色", "无预警"]):
        x = legend_x + i * 128
        ax.add_patch(Rectangle((x, legend_y - 17), 27, 27, facecolor=LEVEL_COLORS[lev], edgecolor="#999999", linewidth=1))
        ax.text(x + 36, legend_y - 4, lev, va="center", fontsize=16, color="#333333")

    left_margin = 70
    region_label_w = 145
    top_y = H - 210
    row_h = 157
    gap_x = 16
    max_cols = 8
    card_w = (W - left_margin * 2 - region_label_w - gap_x * (max_cols - 1)) / max_cols
    card_h = 120

    for r_index, (region, provinces) in enumerate(PROVINCES_BY_REGION.items()):
        y_top = top_y - r_index * row_h
        y = y_top - card_h

        ax.add_patch(FancyBboxPatch(
            (left_margin, y + 8), region_label_w - 15, card_h - 16,
            boxstyle="round,pad=0.012,rounding_size=18",
            facecolor="#222222", edgecolor="none"
        ))
        ax.text(left_margin + (region_label_w - 15) / 2, y + card_h / 2, region,
                ha="center", va="center", fontsize=23, fontweight="bold", color="white")

        for i, prov in enumerate(provinces):
            x = left_margin + region_label_w + i * (card_w + gap_x)
            info = summarize_province(df, prov, province_counts.get(prov, 0))
            count = info["count"]
            has_warning = count > 0

            bg = "#FAF8F0" if has_warning else LEVEL_LIGHT["无预警"]
            edge = "#BBBBBB" if has_warning else "#D4D4D4"

            ax.add_patch(FancyBboxPatch(
                (x, y), card_w, card_h,
                boxstyle="round,pad=0.015,rounding_size=16",
                facecolor=bg, edgecolor=edge, linewidth=1.5 if has_warning else 1
            ))
            _draw_level_bar(ax, x, y, card_h, info["active_levels"])

            name = PROV_SHORT.get(prov, prov[:3])
            ax.text(x + 24, y + card_h - 32, name,
                    ha="left", va="center", fontsize=21, fontweight="bold",
                    color="#111111" if has_warning else "#999999")

            summary_text = info["summary"]
            ax.text(x + 24, y + 42, summary_text,
                    ha="left", va="center", fontsize=14,
                    color="#333333" if has_warning else "#999999", linespacing=1.2)

    note = "说明：左侧色条按红→橙→黄→蓝分段，表示该省当前涉及的预警等级；文字为最多 3 种主要预警（类型·等级）。"
    ax.text(70, 44, note, ha="left", va="center", fontsize=16, color="#555555")

    if _is_file_path(out_path):
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
    save_kw = dict(dpi=dpi, bbox_inches="tight", pad_inches=0.05, facecolor="#FCFBF7")
    if isinstance(out_path, BytesIO):
        save_kw["format"] = "png"
    plt.savefig(out_path, **save_kw)
    plt.close(fig)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--outdir", default="output", help="输出目录")
    parser.add_argument("--csv", default="", help="可选：直接用已有CSV生成图片，不重新抓网页")
    parser.add_argument("--out", default="", help="可选：指定输出图片路径")
    parser.add_argument("--style", choices=["map", "grid"], default="map", help="输出样式：map=地图，grid=分省卡片")
    args = parser.parse_args()

    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    ts = china_now().strftime("%Y%m%d_%H%M")

    if args.csv:
        df = pd.read_csv(args.csv, encoding="utf-8-sig")
        province_counts = df.groupby("province").size().to_dict()
    else:
        try:
            df, province_counts = crawl_all_provinces()
        except RuntimeError as e:
            print(f"错误：{e}")
            raise SystemExit(1) from e
        csv_path = outdir / f"weathercomcn_alarms_{ts}.csv"
        count_path = outdir / f"weathercomcn_province_counts_{ts}.csv"
        df.to_csv(csv_path, index=False, encoding="utf-8-sig")
        pd.DataFrame([{"province": k, "listed_count": v} for k, v in province_counts.items()]).to_csv(
            count_path, index=False, encoding="utf-8-sig"
        )
        print(f"已保存明细：{csv_path}")
        print(f"已保存省级条数：{count_path}")

    out_path = Path(args.out) if args.out else outdir / f"全国各省预警一页图_{ts}.png"
    render_fn = render_map_dashboard if args.style == "map" else render_dashboard
    render_fn(df, province_counts, out_path)
    print(f"已生成图片：{out_path.resolve()}")


if __name__ == "__main__":
    main()
