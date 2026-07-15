from __future__ import annotations

import io
import json
import math
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd
import streamlit as st
from PIL import Image, ImageDraw, ImageFont


# =========================================================
# 기본 설정
# =========================================================
BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
UPLOAD_DIR = DATA_DIR / "uploads"
ASSET_DIR = BASE_DIR / "assets"
DB_PATH = DATA_DIR / "school_safety.db"
DEMO_MAP_PATH = ASSET_DIR / "demo_school_map.png"

DATA_DIR.mkdir(exist_ok=True)
UPLOAD_DIR.mkdir(exist_ok=True)
ASSET_DIR.mkdir(exist_ok=True)

st.set_page_config(
    page_title="학교 안전 이동위험 대시보드",
    page_icon="🏫",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.markdown(
    """
    <style>
    :root {
        --primary: #1f6f5f;
        --secondary: #2f5d8c;
        --accent: #f0b429;
        --orange: #ef7d32;
        --danger: #d64545;
        --border: #dfe7e5;
        --muted: #65747c;
        --bg: #f6f9f8;
    }
    .block-container {padding-top: 1.35rem; padding-bottom: 3rem; max-width: 1320px;}
    .hero {
        padding: 26px 30px; border-radius: 18px; color: white;
        background: linear-gradient(135deg, var(--primary), var(--secondary));
        box-shadow: 0 10px 28px rgba(31,111,95,.16); margin-bottom: 18px;
    }
    .hero h1 {margin: 0 0 7px 0; font-size: 30px;}
    .hero p {margin: 0; color: rgba(255,255,255,.92); font-size: 15.5px;}
    .section-card {
        border: 1px solid var(--border); border-radius: 14px; background: white;
        padding: 18px 20px; margin-bottom: 12px; box-shadow: 0 4px 14px rgba(30,60,55,.045);
    }
    .notice {
        border-left: 5px solid var(--accent); background: #fff9e9;
        border-radius: 9px; padding: 13px 15px; margin: 8px 0 15px;
    }
    .risk-pill {padding: 9px 12px; border-radius: 10px; font-weight: 700; text-align: center;}
    .risk-none {background: #f1f4f3; color: #5f6b68;}
    .risk-watch {background: #fff3a6; color: #745d00;}
    .risk-crowded {background: #ffe0bf; color: #9a4a00;}
    .risk-critical {background: #ffd1d1; color: #a72c2c;}
    .small-muted {color: var(--muted); font-size: .88rem;}
    .legend-item {display:inline-flex; align-items:center; margin-right:18px; font-size:.9rem;}
    .legend-dot {width:14px; height:14px; border-radius:50%; margin-right:6px; border:1px solid rgba(0,0,0,.15);}
    div[data-testid="stMetric"] {border:1px solid var(--border); border-radius:13px; padding:12px 14px; background:white;}
    div[data-testid="stButton"] button {min-height: 44px;}
    </style>
    """,
    unsafe_allow_html=True,
)


# =========================================================
# 상수 및 기본 구조
# =========================================================
WEEKDAYS = ["월", "화", "수", "목", "금"]
PERIODS = ["1교시", "2교시", "3교시", "4교시", "점심", "5교시", "6교시"]
PERIOD_START = {
    "1교시": "09:00",
    "2교시": "09:50",
    "3교시": "10:40",
    "4교시": "11:30",
    "점심": "12:10",
    "5교시": "13:00",
    "6교시": "13:50",
}
TIMETABLE_COLUMNS = [
    "요일",
    "교시",
    "과목",
    "이동수업",
    "도착장소",
    "이동층",
    "이동경로",
    "활동/비고",
]
ZONE_COLUMNS = [
    "구역명",
    "층",
    "구역유형",
    "수용기준(명)",
    "지도X(%)",
    "지도Y(%)",
    "표시반경(%)",
    "메모",
]
ZONE_TYPES = ["복도", "계단", "출입구", "급식실 앞", "강당 앞", "특별교실 앞", "기타"]

DEFAULT_PROFILE = {
    "school_name": "",
    "grade": 1,
    "class_number": 1,
    "class_size": 25,
    "home_floor": 1,
    "classroom_location": "",
    "stairs_per_floor": 20,
    "corridor_type": "일자형",
    "memo": "",
}


# =========================================================
# 데이터베이스
# =========================================================
def get_connection() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db() -> None:
    with get_connection() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS school_profiles (
                profile_key TEXT PRIMARY KEY,
                school_name TEXT NOT NULL,
                grade INTEGER NOT NULL,
                class_number INTEGER NOT NULL,
                profile_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS timetables (
                profile_key TEXT PRIMARY KEY,
                timetable_json TEXT NOT NULL,
                timetable_image_path TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS school_maps (
                school_name TEXT PRIMARY KEY,
                map_image_path TEXT,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS school_zones (
                school_name TEXT PRIMARY KEY,
                zones_json TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()


init_db()


def profile_key(school_name: str, grade: int, class_number: int) -> str:
    clean = school_name.strip() or "미등록학교"
    return f"{clean}__{grade}학년__{class_number}반"


def save_profile(profile: dict[str, Any]) -> str:
    key = profile_key(profile["school_name"], profile["grade"], profile["class_number"])
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO school_profiles
                (profile_key, school_name, grade, class_number, profile_json, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(profile_key) DO UPDATE SET
                school_name=excluded.school_name,
                grade=excluded.grade,
                class_number=excluded.class_number,
                profile_json=excluded.profile_json,
                updated_at=excluded.updated_at
            """,
            (
                key,
                profile["school_name"],
                int(profile["grade"]),
                int(profile["class_number"]),
                json.dumps(profile, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()
    return key


def load_profiles(school_name: str | None = None) -> list[dict[str, Any]]:
    with get_connection() as conn:
        if school_name:
            rows = conn.execute(
                "SELECT profile_key, profile_json FROM school_profiles WHERE school_name=? ORDER BY grade, class_number",
                (school_name,),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT profile_key, profile_json FROM school_profiles ORDER BY school_name, grade, class_number"
            ).fetchall()
    items: list[dict[str, Any]] = []
    for row in rows:
        profile = DEFAULT_PROFILE.copy()
        profile.update(json.loads(row["profile_json"]))
        profile["profile_key"] = row["profile_key"]
        items.append(profile)
    return items


def load_profile(key: str) -> dict[str, Any] | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT profile_json FROM school_profiles WHERE profile_key=?", (key,)
        ).fetchone()
    if not row:
        return None
    profile = DEFAULT_PROFILE.copy()
    profile.update(json.loads(row["profile_json"]))
    return profile


def school_names() -> list[str]:
    return sorted({p["school_name"] for p in load_profiles() if p["school_name"]})


def save_timetable(key: str, timetable: pd.DataFrame, image_path: str | None = None) -> None:
    data = standardize_timetable(timetable).fillna("").to_dict(orient="records")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO timetables (profile_key, timetable_json, timetable_image_path, updated_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(profile_key) DO UPDATE SET
                timetable_json=excluded.timetable_json,
                timetable_image_path=COALESCE(excluded.timetable_image_path, timetables.timetable_image_path),
                updated_at=excluded.updated_at
            """,
            (
                key,
                json.dumps(data, ensure_ascii=False),
                image_path,
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()


def load_timetable(key: str) -> pd.DataFrame:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT timetable_json FROM timetables WHERE profile_key=?", (key,)
        ).fetchone()
    if not row:
        return empty_timetable()
    return standardize_timetable(pd.DataFrame(json.loads(row["timetable_json"])))


def load_timetable_image(key: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT timetable_image_path FROM timetables WHERE profile_key=?", (key,)
        ).fetchone()
    return row["timetable_image_path"] if row else None


def save_school_map(school_name: str, map_path: str | None) -> None:
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO school_maps (school_name, map_image_path, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(school_name) DO UPDATE SET
                map_image_path=COALESCE(excluded.map_image_path, school_maps.map_image_path),
                updated_at=excluded.updated_at
            """,
            (school_name, map_path, datetime.now().isoformat(timespec="seconds")),
        )
        conn.commit()


def load_school_map(school_name: str) -> str | None:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT map_image_path FROM school_maps WHERE school_name=?", (school_name,)
        ).fetchone()
    return row["map_image_path"] if row else None


def save_zones(school_name: str, zones: pd.DataFrame) -> None:
    clean = standardize_zones(zones).fillna("").to_dict(orient="records")
    with get_connection() as conn:
        conn.execute(
            """
            INSERT INTO school_zones (school_name, zones_json, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(school_name) DO UPDATE SET
                zones_json=excluded.zones_json,
                updated_at=excluded.updated_at
            """,
            (
                school_name,
                json.dumps(clean, ensure_ascii=False),
                datetime.now().isoformat(timespec="seconds"),
            ),
        )
        conn.commit()


def load_zones(school_name: str) -> pd.DataFrame:
    with get_connection() as conn:
        row = conn.execute(
            "SELECT zones_json FROM school_zones WHERE school_name=?", (school_name,)
        ).fetchone()
    if not row:
        return empty_zones()
    return standardize_zones(pd.DataFrame(json.loads(row["zones_json"])))


# =========================================================
# 데이터 구조 보정
# =========================================================
def empty_timetable() -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for period in PERIODS:
        for day in WEEKDAYS:
            rows.append(
                {
                    "요일": day,
                    "교시": period,
                    "과목": "급식" if period == "점심" else "",
                    "이동수업": period == "점심",
                    "도착장소": "급식실" if period == "점심" else "",
                    "이동층": 0,
                    "이동경로": "",
                    "활동/비고": "",
                }
            )
    return pd.DataFrame(rows, columns=TIMETABLE_COLUMNS)


def normalize_period(value: Any) -> str:
    text = str(value).strip()
    if text in PERIODS:
        return text
    if text in {"점심시간", "식사시간", "급식"}:
        return "점심"
    try:
        number = int(float(text.replace("교시", "")))
        return f"{number}교시" if f"{number}교시" in PERIODS else text
    except (ValueError, TypeError):
        return text


def to_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"true", "1", "y", "yes", "예", "이동", "o"}


def standardize_timetable(df: pd.DataFrame) -> pd.DataFrame:
    aliases = {
        "장소": "도착장소",
        "수업장소": "도착장소",
        "이동 여부": "이동수업",
        "이동여부": "이동수업",
        "이동 층": "이동층",
        "경로": "이동경로",
        "활동": "활동/비고",
        "비고": "활동/비고",
    }
    frame = df.rename(columns=aliases).copy()
    for col in TIMETABLE_COLUMNS:
        if col not in frame.columns:
            if col == "이동수업":
                frame[col] = False
            elif col == "이동층":
                frame[col] = 0
            else:
                frame[col] = ""
    frame = frame[TIMETABLE_COLUMNS]
    frame["요일"] = frame["요일"].astype(str).str.strip()
    frame["교시"] = frame["교시"].apply(normalize_period)
    frame["이동수업"] = frame["이동수업"].apply(to_bool)
    frame["이동층"] = pd.to_numeric(frame["이동층"], errors="coerce").fillna(0).astype(int)

    base = empty_timetable().set_index(["요일", "교시"])
    valid = frame[
        frame["요일"].isin(WEEKDAYS) & frame["교시"].isin(PERIODS)
    ].drop_duplicates(["요일", "교시"], keep="last")
    if not valid.empty:
        valid = valid.set_index(["요일", "교시"])
        for col in TIMETABLE_COLUMNS[2:]:
            base.loc[valid.index, col] = valid[col]
    return base.reset_index()[TIMETABLE_COLUMNS]


def empty_zones() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {
                "구역명": "중앙복도",
                "층": "2층",
                "구역유형": "복도",
                "수용기준(명)": 45,
                "지도X(%)": 50,
                "지도Y(%)": 46,
                "표시반경(%)": 7,
                "메모": "",
            },
            {
                "구역명": "서쪽계단",
                "층": "전층",
                "구역유형": "계단",
                "수용기준(명)": 28,
                "지도X(%)": 19,
                "지도Y(%)": 48,
                "표시반경(%)": 6,
                "메모": "",
            },
        ],
        columns=ZONE_COLUMNS,
    )


def standardize_zones(df: pd.DataFrame) -> pd.DataFrame:
    frame = df.copy()
    for col in ZONE_COLUMNS:
        if col not in frame.columns:
            frame[col] = ""
    frame = frame[ZONE_COLUMNS]
    frame["수용기준(명)"] = pd.to_numeric(frame["수용기준(명)"], errors="coerce").fillna(30).clip(1, 500).astype(int)
    frame["지도X(%)"] = pd.to_numeric(frame["지도X(%)"], errors="coerce").fillna(50).clip(0, 100)
    frame["지도Y(%)"] = pd.to_numeric(frame["지도Y(%)"], errors="coerce").fillna(50).clip(0, 100)
    frame["표시반경(%)"] = pd.to_numeric(frame["표시반경(%)"], errors="coerce").fillna(6).clip(1, 25)
    return frame[frame["구역명"].astype(str).str.strip() != ""].reset_index(drop=True)


# =========================================================
# 파일 처리
# =========================================================
def save_uploaded_file(uploaded_file: Any, prefix: str) -> str:
    safe_prefix = "".join(c if c.isalnum() or c in "_-" else "_" for c in prefix)
    suffix = Path(uploaded_file.name).suffix.lower() or ".png"
    path = UPLOAD_DIR / f"{safe_prefix}_{datetime.now().strftime('%Y%m%d%H%M%S')}{suffix}"
    path.write_bytes(uploaded_file.getbuffer())
    return str(path)


def read_timetable_file(uploaded_file: Any) -> pd.DataFrame:
    suffix = Path(uploaded_file.name).suffix.lower()
    if suffix == ".csv":
        raw = uploaded_file.getvalue()
        for encoding in ("utf-8-sig", "cp949", "utf-8"):
            try:
                return standardize_timetable(pd.read_csv(io.BytesIO(raw), encoding=encoding))
            except UnicodeDecodeError:
                continue
        raise ValueError("CSV 문자 인코딩을 읽지 못했습니다.")
    if suffix in {".xlsx", ".xlsm"}:
        return standardize_timetable(pd.read_excel(uploaded_file))
    raise ValueError("CSV 또는 XLSX 파일만 지원합니다.")


def image_exists(path: str | None) -> bool:
    return bool(path and Path(path).exists())


# =========================================================
# 이동량 및 위험도 계산
# =========================================================
def split_route(route: Any) -> list[str]:
    text = str(route or "").strip()
    if not text:
        return []
    for separator in ["→", ">", ",", "/", "|"]:
        text = text.replace(separator, "|")
    return [part.strip() for part in text.split("|") if part.strip()]


def risk_level(score: int, people: int) -> tuple[str, str]:
    if people <= 0:
        return "이동 없음", "risk-none"
    if score >= 70:
        return "집중 관리", "risk-critical"
    if score >= 45:
        return "혼잡", "risk-crowded"
    return "주의", "risk-watch"


def compute_movement_risk(
    school_name: str,
    day: str,
    period: str,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    zones = load_zones(school_name)
    profiles = load_profiles(school_name)

    movement_records: list[dict[str, Any]] = []
    zone_stats: dict[str, dict[str, Any]] = {
        str(row["구역명"]): {
            "예상이동인원": 0,
            "관련학급": [],
            "상행": 0,
            "하행": 0,
            "평면이동": 0,
            "저학년인원": 0,
        }
        for _, row in zones.iterrows()
    }

    zone_names = set(zone_stats)
    for profile in profiles:
        timetable = load_timetable(profile["profile_key"])
        matched = timetable[(timetable["요일"] == day) & (timetable["교시"] == period)]
        if matched.empty:
            continue
        row = matched.iloc[0]
        if not to_bool(row["이동수업"]):
            continue

        class_size = int(profile.get("class_size", 25))
        route = split_route(row.get("이동경로", ""))
        destination = str(row.get("도착장소", "")).strip()
        used_zones = [zone for zone in route if zone in zone_names]
        if not used_zones and destination in zone_names:
            used_zones = [destination]

        target_floor = int(pd.to_numeric(row.get("이동층", 0), errors="coerce") or 0)
        home_floor = int(profile.get("home_floor", 1))
        direction = "평면이동"
        if target_floor > home_floor:
            direction = "상행"
        elif 0 < target_floor < home_floor:
            direction = "하행"

        class_label = f"{profile['grade']}-{profile['class_number']}"
        movement_records.append(
            {
                "학급": class_label,
                "학급인원": class_size,
                "과목": row["과목"],
                "도착장소": destination,
                "이동층": target_floor,
                "이동방향": direction,
                "이동경로": " → ".join(route),
                "활동/비고": row["활동/비고"],
            }
        )

        for zone in used_zones:
            stat = zone_stats[zone]
            stat["예상이동인원"] += class_size
            stat["관련학급"].append(class_label)
            stat[direction] += class_size
            if int(profile.get("grade", 1)) <= 2:
                stat["저학년인원"] += class_size

    risk_rows: list[dict[str, Any]] = []
    for idx, zone in zones.iterrows():
        name = str(zone["구역명"])
        stat = zone_stats[name]
        people = int(stat["예상이동인원"])
        capacity = max(int(zone["수용기준(명)"]), 1)
        utilization = people / capacity

        score = 0
        reasons: list[str] = []
        if people > 0:
            score = 18 + min(utilization * 48, 62)
            reasons.append(f"예상 {people}명 이동")
            if str(zone["구역유형"]) == "계단":
                score += 8
                reasons.append("계단 구간")
            if stat["상행"] > 0 and stat["하행"] > 0:
                score += 12
                reasons.append("상·하행 동선 중첩")
            low_share = stat["저학년인원"] / people if people else 0
            if low_share >= 0.35:
                score += 8
                reasons.append("저학년 이동 비중 높음")
            if utilization >= 1:
                reasons.append("수용기준 초과")

        score = int(max(0, min(round(score), 100)))
        level, css_class = risk_level(score, people)
        teacher_count = 0
        if score >= 85 or utilization >= 2:
            teacher_count = 2
        elif score >= 70 or utilization >= 1.2:
            teacher_count = 1

        risk_rows.append(
            {
                "번호": idx + 1,
                **zone.to_dict(),
                "예상이동인원": people,
                "혼잡비율": round(utilization, 2),
                "상행": stat["상행"],
                "하행": stat["하행"],
                "관련학급": ", ".join(stat["관련학급"]),
                "위험점수": score,
                "위험등급": level,
                "CSS": css_class,
                "주요이유": ", ".join(reasons) if reasons else "이동 없음",
                "권장교사": teacher_count,
            }
        )

    risk_df = pd.DataFrame(risk_rows)
    if not risk_df.empty:
        risk_df = risk_df.sort_values(["위험점수", "예상이동인원"], ascending=False).reset_index(drop=True)
    movements_df = pd.DataFrame(movement_records)
    return risk_df, movements_df


def summary_by_period(school_name: str, day: str) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for period in PERIODS:
        risk_df, movements = compute_movement_risk(school_name, day, period)
        if risk_df.empty:
            max_score = 0
            top_zone = "-"
            teachers = 0
            total_people = 0
        else:
            top = risk_df.iloc[0]
            max_score = int(top["위험점수"])
            top_zone = str(top["구역명"])
            teachers = int(risk_df["권장교사"].sum())
            total_people = int(movements["학급인원"].sum()) if not movements.empty else 0
        rows.append(
            {
                "교시": period,
                "시작시각": PERIOD_START[period],
                "이동학급수": len(movements),
                "예상이동학생수": total_people,
                "최고위험점수": max_score,
                "최고위험구간": top_zone,
                "권장배치교사수": teachers,
            }
        )
    return pd.DataFrame(rows)


# =========================================================
# 지도 렌더링
# =========================================================
def risk_rgba(score: int, people: int) -> tuple[int, int, int, int]:
    if people <= 0:
        return (160, 170, 170, 55)
    if score >= 70:
        return (214, 69, 69, 165)
    if score >= 45:
        return (239, 125, 50, 155)
    return (246, 207, 70, 145)


def marker_preview_rgba() -> tuple[int, int, int, int]:
    return (47, 93, 140, 150)


def load_base_map(map_path: str | None) -> Image.Image:
    if image_exists(map_path):
        image = Image.open(map_path).convert("RGBA")
    elif DEMO_MAP_PATH.exists():
        image = Image.open(DEMO_MAP_PATH).convert("RGBA")
    else:
        image = Image.new("RGBA", (1200, 720), "white")
    image.thumbnail((1400, 900))
    return image


def numeric_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "C:/Windows/Fonts/arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    for path in candidates:
        if Path(path).exists():
            try:
                return ImageFont.truetype(path, size=size)
            except OSError:
                pass
    return ImageFont.load_default()


def render_zone_map(
    map_path: str | None,
    zones: pd.DataFrame,
    risk_df: pd.DataFrame | None = None,
    floor_filter: str | None = None,
    preview: bool = False,
) -> Image.Image:
    base = load_base_map(map_path)
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    risk_lookup: dict[str, dict[str, Any]] = {}
    if risk_df is not None and not risk_df.empty:
        risk_lookup = {str(row["구역명"]): row.to_dict() for _, row in risk_df.iterrows()}

    filtered = zones.copy()
    if floor_filter and floor_filter != "전체":
        filtered = filtered[
            filtered["층"].astype(str).isin([floor_filter, "전층", "전체"])
        ]

    font = numeric_font(max(16, int(min(base.size) * 0.028)))
    for original_idx, row in filtered.iterrows():
        x = float(row["지도X(%)"]) / 100 * base.width
        y = float(row["지도Y(%)"]) / 100 * base.height
        radius = float(row["표시반경(%)"]) / 100 * min(base.size)
        radius = max(radius, 16)

        risk = risk_lookup.get(str(row["구역명"]), {})
        score = int(risk.get("위험점수", 0))
        people = int(risk.get("예상이동인원", 0))
        color = marker_preview_rgba() if preview else risk_rgba(score, people)
        outline = (40, 50, 50, 170) if preview else (125, 55, 40, 185)

        draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=color, outline=outline, width=3)
        label = str(int(original_idx) + 1)
        bbox = draw.textbbox((0, 0), label, font=font)
        tw = bbox[2] - bbox[0]
        th = bbox[3] - bbox[1]
        draw.text((x - tw / 2, y - th / 2 - 2), label, fill=(25, 35, 35, 255), font=font)

    return Image.alpha_composite(base, overlay).convert("RGB")


def legend_html() -> str:
    return """
    <div style='margin:8px 0 14px;'>
      <span class='legend-item'><span class='legend-dot' style='background:#f6cf46'></span>주의</span>
      <span class='legend-item'><span class='legend-dot' style='background:#ef7d32'></span>혼잡</span>
      <span class='legend-item'><span class='legend-dot' style='background:#d64545'></span>집중 관리</span>
      <span class='legend-item'><span class='legend-dot' style='background:#a0aaaa'></span>이동 없음</span>
    </div>
    """


# =========================================================
# 공통 UI
# =========================================================
def hero() -> None:
    st.markdown(
        """
        <div class="hero">
          <h1>학교 시간표 기반 교내 이동위험 대시보드</h1>
          <p>전체 학급의 이동수업을 합산해 특정 시간대의 복도·계단 예상 이동량을 계산하고, 학교 지도 위에 위험구간과 교사 배치 권고를 표시합니다.</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def profile_label(profile: dict[str, Any]) -> str:
    return f"{profile['school_name']} · {profile['grade']}학년 {profile['class_number']}반"


def select_school(key: str) -> str | None:
    names = school_names()
    if not names:
        st.warning("등록된 학교가 없습니다. 왼쪽 ‘관리자 설정’에서 예시 데이터를 만들거나 학교·학급을 등록해 주세요.")
        return None
    return st.selectbox("학교", names, key=key)


def select_profile(key: str) -> tuple[str, dict[str, Any]] | None:
    profiles = load_profiles()
    if not profiles:
        st.warning("등록된 학급이 없습니다. 왼쪽 ‘관리자 설정’에서 먼저 등록해 주세요.")
        return None
    keys = [p["profile_key"] for p in profiles]
    labels = {p["profile_key"]: profile_label(p) for p in profiles}
    selected_key = st.selectbox("학급", keys, format_func=lambda x: labels[x], key=key)
    profile = load_profile(selected_key)
    if not profile:
        st.error("학급 정보를 불러오지 못했습니다.")
        return None
    return selected_key, profile


def current_day_period_defaults() -> tuple[str, str]:
    now = datetime.now()
    day_map = {0: "월", 1: "화", 2: "수", 3: "목", 4: "금"}
    day = day_map.get(now.weekday(), "월")
    hhmm = now.strftime("%H:%M")
    ordered = list(PERIOD_START.items())
    period = "1교시"
    for name, start in ordered:
        if hhmm <= start:
            period = name
            break
        period = name
    if hhmm > "14:30":
        period = "1교시"
    return day, period


def risk_top_cards(risk_df: pd.DataFrame, top_n: int = 3) -> None:
    if risk_df.empty or int(risk_df["예상이동인원"].sum()) == 0:
        st.info("선택한 시간대에 등록된 이동수업이 없습니다.")
        return
    top = risk_df[risk_df["예상이동인원"] > 0].head(top_n)
    columns = st.columns(top_n)
    for col, (_, row) in zip(columns, top.iterrows()):
        with col:
            st.markdown(f"#### {row['구역명']}")
            st.metric("예상 이동 인원", f"{int(row['예상이동인원'])}명")
            st.markdown(
                f"<div class='risk-pill {row['CSS']}'>{row['위험등급']} · {int(row['위험점수'])}점</div>",
                unsafe_allow_html=True,
            )
            st.caption(str(row["주요이유"]))


def teacher_recommendations(risk_df: pd.DataFrame) -> None:
    targets = risk_df[risk_df["권장교사"] > 0] if not risk_df.empty else pd.DataFrame()
    if targets.empty:
        st.success("현재 시간대에는 별도의 교사 배치가 필요한 구간이 없습니다.")
        return
    for _, row in targets.iterrows():
        st.warning(
            f"**{row['구역명']}에 교사 {int(row['권장교사'])}명 배치 권장**  \n"
            f"예상 {int(row['예상이동인원'])}명 · 관련 학급 {row['관련학급'] or '-'} · {row['주요이유']}"
        )


# =========================================================
# 메인 페이지 1: 오늘의 안전 현황
# =========================================================
def page_today() -> None:
    st.header("오늘의 안전 현황")
    school = select_school("today_school")
    if not school:
        return

    default_day, default_period = current_day_period_defaults()
    c1, c2 = st.columns(2)
    day = c1.selectbox("요일", WEEKDAYS, index=WEEKDAYS.index(default_day), key="today_day")
    period = c2.selectbox(
        "이동 시간대",
        PERIODS,
        index=PERIODS.index(default_period),
        format_func=lambda x: f"{x} 시작 전 · {PERIOD_START[x]}",
        key="today_period",
    )

    risk_df, movements = compute_movement_risk(school, day, period)
    map_path = load_school_map(school)
    zones = load_zones(school)

    registered_classes = len(load_profiles(school))
    moving_classes = len(movements)
    moving_students = int(movements["학급인원"].sum()) if not movements.empty else 0
    max_score = int(risk_df["위험점수"].max()) if not risk_df.empty else 0

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("등록 학급", f"{registered_classes}개")
    m2.metric("이동 학급", f"{moving_classes}개")
    m3.metric("예상 이동 학생", f"{moving_students}명")
    m4.metric("최고 위험점수", f"{max_score}점")

    left, right = st.columns([1.45, 1])
    with left:
        st.subheader("시간대별 교내 위험지도")
        st.markdown(legend_html(), unsafe_allow_html=True)
        floor_options = ["전체"] + sorted(zones["층"].astype(str).unique().tolist())
        floor = st.selectbox("표시 층", floor_options, key="today_floor")
        image = render_zone_map(map_path, zones, risk_df, floor_filter=floor)
        st.image(image, width="stretch")
        st.caption("지도 번호는 오른쪽 위험구간 목록의 번호와 대응합니다.")

    with right:
        st.subheader("위험구간 Top 3")
        visible = risk_df[risk_df["예상이동인원"] > 0].head(3)
        if visible.empty:
            st.info("표시할 이동 위험구간이 없습니다.")
        else:
            for _, row in visible.iterrows():
                st.markdown(
                    f"<div class='section-card'><b>{int(row['번호'])}. {row['구역명']}</b><br>"
                    f"예상 {int(row['예상이동인원'])}명 · {row['위험등급']} {int(row['위험점수'])}점<br>"
                    f"<span class='small-muted'>{row['주요이유']}</span></div>",
                    unsafe_allow_html=True,
                )

        st.subheader("교사 배치 권고")
        teacher_recommendations(risk_df)

    st.subheader("이동 학급 요약")
    if movements.empty:
        st.info("선택한 시간대에 이동수업으로 등록된 학급이 없습니다.")
    else:
        st.dataframe(movements, width="stretch", hide_index=True)


# =========================================================
# 메인 페이지 2: 5×7 시간표
# =========================================================
def subject_matrix(timetable: pd.DataFrame) -> pd.DataFrame:
    matrix = timetable.pivot(index="교시", columns="요일", values="과목")
    matrix = matrix.reindex(index=PERIODS, columns=WEEKDAYS).fillna("")
    matrix.index.name = "교시"
    return matrix.reset_index()


def apply_subject_matrix(timetable: pd.DataFrame, matrix: pd.DataFrame) -> pd.DataFrame:
    updated = timetable.copy()
    for _, row in matrix.iterrows():
        period = str(row["교시"])
        for day in WEEKDAYS:
            mask = (updated["요일"] == day) & (updated["교시"] == period)
            updated.loc[mask, "과목"] = str(row.get(day, "") or "").strip()
    return standardize_timetable(updated)


def timetable_cell(timetable: pd.DataFrame, day: str, period: str) -> pd.Series:
    row = timetable[(timetable["요일"] == day) & (timetable["교시"] == period)]
    if row.empty:
        return pd.Series({col: "" for col in TIMETABLE_COLUMNS})
    return row.iloc[0]


def page_timetable() -> None:
    st.header("5×7 학급 시간표")
    selected = select_profile("timetable_profile")
    if not selected:
        return
    key, profile = selected
    timetable = load_timetable(key)
    zones = load_zones(profile["school_name"])
    zone_options = zones["구역명"].astype(str).tolist()

    st.markdown(
        f"<div class='notice'><b>{profile_label(profile)}</b><br>셀의 ↗ 표시는 이동수업입니다. 셀을 누르면 이동층·장소·경로·활동을 확인하거나 수정할 수 있습니다.</div>",
        unsafe_allow_html=True,
    )

    with st.expander("과목을 5×7 표에서 빠르게 입력", expanded=False):
        matrix = subject_matrix(timetable)
        edited_matrix = st.data_editor(
            matrix,
            width="stretch",
            hide_index=True,
            num_rows="fixed",
            disabled=["교시"],
            key=f"subject_matrix_{key}",
        )
        if st.button("과목표 반영", type="primary", key=f"apply_matrix_{key}"):
            timetable = apply_subject_matrix(timetable, edited_matrix)
            save_timetable(key, timetable)
            st.success("과목을 시간표에 반영했습니다.")
            st.rerun()

    header = st.columns([0.65, 1, 1, 1, 1, 1])
    header[0].markdown("**교시**")
    for idx, day in enumerate(WEEKDAYS, start=1):
        header[idx].markdown(f"**{day}**")

    selected_state_key = f"selected_cell_{key}"
    if selected_state_key not in st.session_state:
        st.session_state[selected_state_key] = ("월", "1교시")

    for period in PERIODS:
        cols = st.columns([0.65, 1, 1, 1, 1, 1])
        cols[0].markdown(f"**{period}**")
        for idx, day in enumerate(WEEKDAYS, start=1):
            cell = timetable_cell(timetable, day, period)
            subject = str(cell.get("과목", "")).strip() or "＋ 입력"
            marker = " ↗" if to_bool(cell.get("이동수업", False)) else ""
            if cols[idx].button(
                f"{subject}{marker}",
                key=f"cell_{key}_{day}_{period}",
                width="stretch",
                help="눌러서 세부정보 확인·수정",
            ):
                st.session_state[selected_state_key] = (day, period)

    selected_day, selected_period = st.session_state[selected_state_key]
    cell = timetable_cell(timetable, selected_day, selected_period)

    st.divider()
    st.subheader(f"{selected_day}요일 {selected_period} 세부정보")
    with st.form(f"cell_form_{key}_{selected_day}_{selected_period}"):
        c1, c2 = st.columns([1.2, 1])
        subject = c1.text_input("과목", value=str(cell.get("과목", "")))
        moving = c2.checkbox("이동수업", value=to_bool(cell.get("이동수업", False)))

        c3, c4 = st.columns(2)
        destination = c3.text_input("도착 장소", value=str(cell.get("도착장소", "")), placeholder="예: 강당, 과학실")
        target_floor = c4.number_input(
            "이동층",
            min_value=0,
            max_value=20,
            value=int(pd.to_numeric(cell.get("이동층", 0), errors="coerce") or 0),
            step=1,
            help="이동수업이 아니면 0",
        )

        existing_route = split_route(cell.get("이동경로", ""))
        available_routes = list(dict.fromkeys(zone_options + existing_route))
        route_selected = st.multiselect(
            "이용하는 복도·계단 순서",
            options=available_routes,
            default=existing_route,
            help="선택한 순서대로 이동경로가 저장됩니다. 학교 구역은 관리자 설정에서 등록합니다.",
        )
        activity = st.text_area(
            "활동/비고",
            value=str(cell.get("활동/비고", "")),
            placeholder="예: 피구, 실험 / 다른 학급과 공동 사용",
        )
        saved = st.form_submit_button("이 셀 저장", type="primary", width="stretch")

    if saved:
        mask = (timetable["요일"] == selected_day) & (timetable["교시"] == selected_period)
        timetable.loc[mask, "과목"] = subject.strip()
        timetable.loc[mask, "이동수업"] = bool(moving)
        timetable.loc[mask, "도착장소"] = destination.strip()
        timetable.loc[mask, "이동층"] = int(target_floor)
        timetable.loc[mask, "이동경로"] = " → ".join(route_selected)
        timetable.loc[mask, "활동/비고"] = activity.strip()
        save_timetable(key, timetable)
        st.success("시간표 셀을 저장했습니다.")
        st.rerun()

    with st.expander("시간표 사진 또는 파일 불러오기", expanded=False):
        tab1, tab2 = st.tabs(["사진 첨부", "CSV·Excel"])
        with tab1:
            image_file = st.file_uploader("시간표 사진", type=["png", "jpg", "jpeg", "webp"], key=f"tt_image_{key}")
            if image_file:
                st.image(image_file, caption="첨부한 시간표", width="stretch")
                if st.button("시간표 사진 저장", key=f"save_tt_image_{key}"):
                    image_path = save_uploaded_file(image_file, f"{key}_timetable")
                    save_timetable(key, timetable, image_path=image_path)
                    st.success("사진을 저장했습니다. 자동 글자 인식은 아직 포함하지 않았습니다.")
            saved_image = load_timetable_image(key)
            if image_exists(saved_image):
                st.caption("현재 저장된 시간표 사진")
                st.image(saved_image, width="stretch")
        with tab2:
            file = st.file_uploader("시간표 CSV·Excel", type=["csv", "xlsx", "xlsm"], key=f"tt_file_{key}")
            if file:
                try:
                    imported = read_timetable_file(file)
                    st.dataframe(imported, width="stretch", hide_index=True)
                    if st.button("불러온 시간표로 교체", type="primary", key=f"replace_tt_{key}"):
                        save_timetable(key, imported)
                        st.success("시간표를 교체했습니다.")
                        st.rerun()
                except Exception as exc:
                    st.error(f"파일을 읽지 못했습니다: {exc}")


# =========================================================
# 메인 페이지 3: 교내 위험지도
# =========================================================
def page_risk_map() -> None:
    st.header("교내 이동 위험지도")
    school = select_school("map_school")
    if not school:
        return
    zones = load_zones(school)
    map_path = load_school_map(school)

    c1, c2, c3 = st.columns(3)
    day = c1.selectbox("요일", WEEKDAYS, key="map_day")
    period = c2.selectbox(
        "이동 시간대",
        PERIODS,
        format_func=lambda x: f"{x} 시작 전 · {PERIOD_START[x]}",
        key="map_period",
    )
    floors = ["전체"] + sorted(zones["층"].astype(str).unique().tolist())
    floor = c3.selectbox("표시 층", floors, key="map_floor")

    risk_df, movements = compute_movement_risk(school, day, period)
    st.markdown(legend_html(), unsafe_allow_html=True)
    image = render_zone_map(map_path, zones, risk_df, floor_filter=floor)
    st.image(image, width="stretch")

    st.subheader("구역별 위험수치")
    display_cols = [
        "번호",
        "구역명",
        "층",
        "구역유형",
        "예상이동인원",
        "수용기준(명)",
        "상행",
        "하행",
        "위험점수",
        "위험등급",
        "권장교사",
        "관련학급",
    ]
    if risk_df.empty:
        st.info("등록된 위험구역이 없습니다.")
    else:
        st.dataframe(risk_df[display_cols], width="stretch", hide_index=True)

    st.subheader("교사 배치 제안")
    teacher_recommendations(risk_df)


# =========================================================
# 메인 페이지 4: 위험 분석
# =========================================================
def page_risk_analysis() -> None:
    st.header("시간대별 위험 분석")
    school = select_school("analysis_school")
    if not school:
        return
    day = st.selectbox("분석 요일", WEEKDAYS, key="analysis_day")
    summary = summary_by_period(school, day)

    c1, c2, c3 = st.columns(3)
    peak = summary.loc[summary["최고위험점수"].idxmax()]
    c1.metric("최고 위험 시간대", str(peak["교시"]))
    c2.metric("최고 위험구간", str(peak["최고위험구간"]))
    c3.metric("최고 위험점수", f"{int(peak['최고위험점수'])}점")

    chart = summary.set_index("교시")[["최고위험점수"]]
    st.subheader(f"{day}요일 시간대별 최고 위험점수")
    st.bar_chart(chart, height=320)

    st.dataframe(summary, width="stretch", hide_index=True)

    period = st.selectbox(
        "세부 분석 시간대",
        PERIODS,
        format_func=lambda x: f"{x} 시작 전 · {PERIOD_START[x]}",
        key="analysis_period",
    )
    risk_df, movements = compute_movement_risk(school, day, period)
    risk_top_cards(risk_df)

    left, right = st.columns(2)
    with left:
        st.subheader("관련 이동 학급")
        if movements.empty:
            st.info("이동 학급이 없습니다.")
        else:
            st.dataframe(movements, width="stretch", hide_index=True)
    with right:
        st.subheader("배치 및 운영 권고")
        teacher_recommendations(risk_df)
        if not risk_df.empty and int(risk_df["예상이동인원"].sum()) > 0:
            top = risk_df.iloc[0]
            if int(top["상행"]) > 0 and int(top["하행"]) > 0:
                st.info("상행과 하행 동선이 겹칩니다. 이동 시작 시각을 1~2분 차등 적용하는 방안을 검토하세요.")
            elif int(top["예상이동인원"]) >= int(top["수용기준(명)"]):
                st.info("수용기준을 초과합니다. 대체 계단·복도 사용 또는 학급별 이동 순서 조정을 검토하세요.")


# =========================================================
# 관리자 설정 1: 학교·학급 등록
# =========================================================
def settings_profiles() -> None:
    st.header("관리자 설정 · 학교·학급")
    profiles = load_profiles()
    options = ["새 학급 등록"] + [p["profile_key"] for p in profiles]
    labels = {p["profile_key"]: profile_label(p) for p in profiles}
    mode = st.selectbox(
        "등록 모드",
        options,
        format_func=lambda x: x if x == "새 학급 등록" else f"기존 수정 · {labels[x]}",
    )

    initial = DEFAULT_PROFILE.copy()
    if mode != "새 학급 등록":
        initial.update(load_profile(mode) or {})

    with st.form("profile_form"):
        c1, c2, c3, c4 = st.columns(4)
        school_name = c1.text_input("학교명 *", value=initial["school_name"], placeholder="예: 안전초등학교")
        grade = c2.number_input("학년", 1, 6, int(initial["grade"]), 1)
        class_number = c3.number_input("반", 1, 30, int(initial["class_number"]), 1)
        class_size = c4.number_input("학급 인원", 1, 60, int(initial["class_size"]), 1)

        c5, c6 = st.columns(2)
        home_floor = c5.number_input("주 교실 층", 1, 20, int(initial["home_floor"]), 1)
        classroom_location = c6.text_input("주 교실 위치", value=initial["classroom_location"], placeholder="예: 3층 동쪽")

        c7, c8 = st.columns(2)
        stairs = c7.number_input("한 층당 계단 수", 1, 100, int(initial["stairs_per_floor"]), 1)
        corridor_options = ["일자형", "L자형", "U자형", "복합형"]
        current_corridor = initial["corridor_type"] if initial["corridor_type"] in corridor_options else "일자형"
        corridor = c8.selectbox("복도 구조", corridor_options, index=corridor_options.index(current_corridor))
        memo = st.text_area("메모", value=initial["memo"])
        submitted = st.form_submit_button("저장", type="primary", width="stretch")

    if submitted:
        if not school_name.strip():
            st.error("학교명을 입력해 주세요.")
        else:
            profile = {
                "school_name": school_name.strip(),
                "grade": int(grade),
                "class_number": int(class_number),
                "class_size": int(class_size),
                "home_floor": int(home_floor),
                "classroom_location": classroom_location.strip(),
                "stairs_per_floor": int(stairs),
                "corridor_type": corridor,
                "memo": memo.strip(),
            }
            key = save_profile(profile)
            if not load_timetable(key).shape[0]:
                save_timetable(key, empty_timetable())
            st.success(f"{school_name} {grade}학년 {class_number}반을 저장했습니다.")

    if profiles:
        st.subheader("등록된 학급")
        table = pd.DataFrame(
            [
                {
                    "학교": p["school_name"],
                    "학년": p["grade"],
                    "반": p["class_number"],
                    "인원": p["class_size"],
                    "주 교실 층": p["home_floor"],
                    "교실 위치": p["classroom_location"],
                }
                for p in profiles
            ]
        )
        st.dataframe(table, width="stretch", hide_index=True)


# =========================================================
# 관리자 설정 2: 지도·구역
# =========================================================
def settings_map_zones() -> None:
    st.header("관리자 설정 · 학교 지도와 이동구역")
    school = select_school("settings_map_school")
    if not school:
        return
    map_path = load_school_map(school)
    zones = load_zones(school)

    st.markdown(
        """
        <div class='notice'>
        복도·계단별 <b>수용기준</b>과 지도상의 X·Y 위치를 최초 1회 등록합니다.
        X=0은 지도 왼쪽, X=100은 오른쪽이며 Y=0은 위쪽, Y=100은 아래쪽입니다.
        </div>
        """,
        unsafe_allow_html=True,
    )

    left, right = st.columns([1.1, 1])
    new_map_path: str | None = None
    with left:
        upload = st.file_uploader("학교 구조도 또는 층별 지도", type=["png", "jpg", "jpeg", "webp"], key="school_map_upload")
        if upload:
            st.image(upload, caption="새로 첨부한 지도", width="stretch")
            new_map_path = save_uploaded_file(upload, f"{school}_map")
        elif image_exists(map_path):
            st.image(map_path, caption="현재 저장된 지도", width="stretch")
        else:
            st.image(DEMO_MAP_PATH, caption="지도 미등록 · 예시 도면", width="stretch")

    with right:
        st.markdown("#### 구역 등록 기준")
        st.write("- 복도와 계단을 이동량 집계 단위로 나눕니다.")
        st.write("- 시간대별로 사람이 지나갈 수 있는 기준 인원을 입력합니다.")
        st.write("- 학급 시간표의 ‘이동경로’에서 동일한 구역명을 선택합니다.")
        st.write("- 지도 좌표는 위험 원을 표시할 중심점입니다.")

    edited = st.data_editor(
        zones,
        width="stretch",
        hide_index=True,
        num_rows="dynamic",
        column_config={
            "구역유형": st.column_config.SelectboxColumn("구역유형", options=ZONE_TYPES),
            "수용기준(명)": st.column_config.NumberColumn("수용기준(명)", min_value=1, max_value=500, step=1),
            "지도X(%)": st.column_config.NumberColumn("지도X(%)", min_value=0, max_value=100, step=1),
            "지도Y(%)": st.column_config.NumberColumn("지도Y(%)", min_value=0, max_value=100, step=1),
            "표시반경(%)": st.column_config.NumberColumn("표시반경(%)", min_value=1, max_value=25, step=1),
        },
        key=f"zones_editor_{school}",
    )

    c1, c2 = st.columns(2)
    if c1.button("지도·구역 저장", type="primary", width="stretch"):
        save_zones(school, edited)
        save_school_map(school, new_map_path)
        st.success("학교 지도와 이동구역을 저장했습니다.")
        st.rerun()

    if c2.button("현재 좌표 미리보기", width="stretch"):
        st.session_state["show_zone_preview"] = True

    if st.session_state.get("show_zone_preview", False):
        preview = render_zone_map(new_map_path or map_path, standardize_zones(edited), preview=True)
        st.image(preview, caption="구역 좌표 미리보기", width="stretch")
        preview_table = standardize_zones(edited).copy()
        preview_table.insert(0, "번호", range(1, len(preview_table) + 1))
        st.dataframe(preview_table[["번호", "구역명", "층", "구역유형", "지도X(%)", "지도Y(%)"]], width="stretch", hide_index=True)


# =========================================================
# 관리자 설정 3: 데이터 관리·예시 데이터
# =========================================================
def make_demo_map() -> None:
    if DEMO_MAP_PATH.exists():
        return
    image = Image.new("RGB", (1200, 720), "#f3f4f1")
    draw = ImageDraw.Draw(image)
    # 바깥 건물
    draw.rounded_rectangle((70, 85, 1130, 635), radius=28, fill="#ffffff", outline="#75848a", width=5)
    # 중앙 복도
    draw.rounded_rectangle((180, 305, 1020, 415), radius=16, fill="#dce8e4", outline="#8fa6a0", width=3)
    # 교실 블록
    for x in [190, 390, 590, 790]:
        draw.rounded_rectangle((x, 125, x + 155, 275), radius=12, fill="#e8eef4", outline="#8296a8", width=3)
        draw.rounded_rectangle((x, 445, x + 155, 595), radius=12, fill="#f3eadc", outline="#a99a7f", width=3)
    # 계단
    draw.rounded_rectangle((95, 270, 165, 450), radius=10, fill="#e6dff0", outline="#8c7b9d", width=3)
    draw.rounded_rectangle((1035, 270, 1105, 450), radius=10, fill="#e6dff0", outline="#8c7b9d", width=3)
    # 급식실/강당
    draw.rounded_rectangle((70, 490, 170, 635), radius=12, fill="#ffe5cf", outline="#b48562", width=3)
    draw.rounded_rectangle((1030, 90, 1130, 250), radius=12, fill="#dff0d8", outline="#7a9a6e", width=3)
    image.save(DEMO_MAP_PATH)


def seed_demo_data() -> None:
    make_demo_map()
    school = "안전초등학교"
    demo_profiles = [
        (1, 1, 24, 2, "2층 동쪽"),
        (2, 1, 26, 2, "2층 중앙"),
        (3, 1, 28, 3, "3층 서쪽"),
        (4, 1, 29, 3, "3층 중앙"),
        (5, 1, 30, 4, "4층 동쪽"),
        (6, 1, 28, 4, "4층 중앙"),
    ]
    keys: dict[int, str] = {}
    for grade, cls, size, floor, location in demo_profiles:
        profile = {
            "school_name": school,
            "grade": grade,
            "class_number": cls,
            "class_size": size,
            "home_floor": floor,
            "classroom_location": location,
            "stairs_per_floor": 20,
            "corridor_type": "복합형",
            "memo": "예시 데이터",
        }
        keys[grade] = save_profile(profile)

    zones = pd.DataFrame(
        [
            ["2층 중앙복도", "2층", "복도", 55, 50, 48, 8, ""],
            ["3층 중앙복도", "3층", "복도", 55, 50, 48, 8, ""],
            ["서쪽계단", "전층", "계단", 32, 12, 50, 7, ""],
            ["동쪽계단", "전층", "계단", 32, 88, 50, 7, ""],
            ["급식실 앞", "1층", "급식실 앞", 60, 12, 79, 7, ""],
            ["강당 앞", "1층", "강당 앞", 45, 88, 20, 7, ""],
        ],
        columns=ZONE_COLUMNS,
    )
    save_zones(school, zones)
    save_school_map(school, str(DEMO_MAP_PATH))

    # 화요일 3교시: 여러 학급이 서쪽계단과 복도에 집중
    for grade, key in keys.items():
        tt = empty_timetable()
        assignments = {
            ("월", "1교시"): ("국어", False, "", 0, "", ""),
            ("월", "2교시"): ("수학", False, "", 0, "", ""),
            ("화", "3교시"): (
                "체육" if grade in {1, 2, 3} else "과학",
                True,
                "강당" if grade in {1, 2, 3} else "과학실",
                1 if grade in {1, 2, 3} else 2,
                "3층 중앙복도 → 서쪽계단 → 강당 앞" if grade >= 3 else "2층 중앙복도 → 서쪽계단 → 강당 앞",
                "피구" if grade in {1, 2, 3} else "실험",
            ),
            ("목", "5교시"): ("음악", True, "음악실", 2, "동쪽계단 → 2층 중앙복도", "합주"),
        }
        for (day, period), values in assignments.items():
            mask = (tt["요일"] == day) & (tt["교시"] == period)
            tt.loc[mask, ["과목", "이동수업", "도착장소", "이동층", "이동경로", "활동/비고"]] = values
        # 점심 이동
        for day in WEEKDAYS:
            mask = (tt["요일"] == day) & (tt["교시"] == "점심")
            route = "2층 중앙복도 → 서쪽계단 → 급식실 앞" if grade <= 2 else "3층 중앙복도 → 서쪽계단 → 급식실 앞"
            if grade >= 5:
                route = "동쪽계단 → 급식실 앞"
            tt.loc[mask, ["과목", "이동수업", "도착장소", "이동층", "이동경로", "활동/비고"]] = (
                "급식",
                True,
                "급식실",
                1,
                route,
                "점심 이동",
            )
        save_timetable(key, tt)


def settings_data() -> None:
    st.header("관리자 설정 · 데이터 관리")
    st.subheader("예시 데이터")
    st.write("앱을 바로 확인할 수 있도록 6개 학급, 이동시간표, 학교 지도를 생성합니다.")
    if st.button("예시 데이터 생성", type="primary"):
        seed_demo_data()
        st.success("예시 데이터를 생성했습니다. 메인 화면에서 안전초등학교를 선택하세요.")
        st.rerun()

    schools = school_names()
    if not schools:
        return
    school = st.selectbox("내보낼 학교", schools, key="export_school")
    profiles = load_profiles(school)
    payload = {
        "school": school,
        "profiles": profiles,
        "zones": load_zones(school).fillna("").to_dict(orient="records"),
        "map_path": load_school_map(school),
        "timetables": {
            p["profile_key"]: load_timetable(p["profile_key"]).fillna("").to_dict(orient="records")
            for p in profiles
        },
        "exported_at": datetime.now().isoformat(timespec="seconds"),
    }
    st.download_button(
        "학교 전체 데이터 JSON 내려받기",
        data=json.dumps(payload, ensure_ascii=False, indent=2).encode("utf-8"),
        file_name=f"{school}_안전대시보드_전체데이터.json",
        mime="application/json",
        width="stretch",
    )
    st.caption("로컬 입력정보는 프로젝트 폴더의 data/school_safety.db에 저장됩니다.")


# =========================================================
# 메인
# =========================================================
def main() -> None:
    make_demo_map()
    hero()

    main_pages = ["오늘의 안전 현황", "5×7 시간표", "교내 위험지도", "위험 분석"]
    page = st.sidebar.radio("메뉴", main_pages)

    with st.sidebar.expander("⚙ 관리자 설정", expanded=False):
        open_settings = st.checkbox("설정 화면 열기")
        settings_page = st.radio(
            "설정 항목",
            ["학교·학급", "학교 지도·이동구역", "데이터 관리"],
            label_visibility="collapsed",
        )

    st.sidebar.divider()
    st.sidebar.caption("위험수치는 등록된 전체 학급의 이동수업과 구역별 수용기준을 이용한 상대적 이동위험지수입니다.")
    st.sidebar.caption("실제 사고발생 확률은 아닙니다.")

    if open_settings:
        if settings_page == "학교·학급":
            settings_profiles()
        elif settings_page == "학교 지도·이동구역":
            settings_map_zones()
        else:
            settings_data()
        return

    if page == "오늘의 안전 현황":
        page_today()
    elif page == "5×7 시간표":
        page_timetable()
    elif page == "교내 위험지도":
        page_risk_map()
    else:
        page_risk_analysis()


if __name__ == "__main__":
    main()
