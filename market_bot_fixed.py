import os
import sys
import json
import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import requests
import yfinance as yf
from pykrx import stock

TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN", "")
CHAT_ID = os.environ.get("TELEGRAM_CHAT_ID", "")
KRX_API_KEY = os.environ.get("KRX_API_KEY", "")

HK = ZoneInfo("Asia/Hong_Kong")

KOSPI_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd"
KOSDAQ_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/kosdaq_dd_trd"
DERIVATIVE_INDEX_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/drvprod_dd_trd"
SNAPSHOT_FILE = "flow_snapshot.json"

KRX_DERIVATIVE_INDEX_TARGETS = [
    "미국달러선물지수",
    "엔선물지수",
    "유로선물지수",
    "코스피 200 선물지수",
    "코스닥 150 선물지수",
]

KRX_NIGHT_DERIVATIVE_INDEX_TARGETS = [
    {
        "display_name": "코스피200선물지수 (야간)",
        "aliases": [
            "코스피 200 선물지수 (야간)",
            "코스피200선물지수 (야간)",
            "코스피200 야간선물지수",
            "코스피 200 야간선물지수",
        ],
    },
    {
        "display_name": "코스닥150선물지수 (야간)",
        "aliases": [
            "코스닥 150 선물지수 (야간)",
            "코스닥150선물지수 (야간)",
            "코스닥150 야간선물지수",
            "코스닥 150 야간선물지수",
        ],
    },
]


def split_telegram_text(text, limit=3500):
    lines = text.splitlines()
    chunks = []
    current = ""

    for line in lines:
        candidate = f"{current}\n{line}" if current else line
        if len(candidate) <= limit:
            current = candidate
        else:
            if current:
                chunks.append(current)
            current = line

    if current:
        chunks.append(current)

    return chunks or [text[:limit]]


def send_telegram(text):
    if not TOKEN or not CHAT_ID:
        raise ValueError("TELEGRAM_BOT_TOKEN 또는 TELEGRAM_CHAT_ID 환경변수가 없습니다.")

    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    for chunk in split_telegram_text(text):
        res = requests.post(
            url,
            json={
                "chat_id": CHAT_ID,
                "text": chunk,
                "parse_mode": "HTML",
                "disable_web_page_preview": True,
            },
            timeout=20,
        )

        if not res.ok:
            print("Telegram error:", res.status_code, res.text)

        res.raise_for_status()


def direction_emoji(value):
    return "🟢" if float(value) >= 0 else "🔴"


def parse_num(value):
    if value is None:
        return 0.0

    value = str(value).replace(",", "").replace("%", "").strip()

    if value in ["", "-", "nan", "None"]:
        return 0.0

    try:
        return float(value)
    except ValueError:
        return 0.0


def fmt_amount(value):
    value = float(value)
    emoji = direction_emoji(value)
    abs_value = abs(value)

    if abs_value >= 1_000_000_000_000:
        formatted = f"₩{abs_value / 1_000_000_000_000:,.1f}trn"
    elif abs_value >= 1_000_000_000:
        formatted = f"₩{abs_value / 1_000_000_000:,.1f}bn"
    elif abs_value >= 1_000_000:
        formatted = f"₩{abs_value / 1_000_000:,.1f}mn"
    elif abs_value >= 1_000:
        formatted = f"₩{abs_value / 1_000:,.1f}k"
    else:
        formatted = f"₩{abs_value:,.0f}"

    sign = "+" if value >= 0 else "-"
    return f"{emoji} {sign}{formatted}"


def fmt_flow_gap(final_value, previous_value):
    final_value = float(final_value)
    previous_value = float(previous_value)
    diff = final_value - previous_value

    if previous_value != 0:
        gap_pct = diff / abs(previous_value) * 100
    else:
        gap_pct = 0.0

    return f"{fmt_amount(diff)} ({direction_emoji(diff)} {gap_pct:+.2f}%)"


def save_flow_snapshot(kospi_flow, kosdaq_flow):
    snapshot = {
        "date": datetime.now(HK).strftime("%Y%m%d"),
        "saved_at": datetime.now(HK).strftime("%Y-%m-%d %H:%M:%S"),
        "kospi_flow": {
            "개인": float(kospi_flow.get("개인", 0)),
            "기관": float(kospi_flow.get("기관", 0)),
            "외국인": float(kospi_flow.get("외국인", 0)),
        },
        "kosdaq_flow": {
            "개인": float(kosdaq_flow.get("개인", 0)),
            "기관": float(kosdaq_flow.get("기관", 0)),
            "외국인": float(kosdaq_flow.get("외국인", 0)),
        },
    }

    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def load_flow_snapshot():
    if not os.path.exists(SNAPSHOT_FILE):
        return None

    with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def get_yf_close_pct(ticker):
    fallback_map = {
        "^KS11": ["^KS11", "KOSPI.KS"],
        "^KQ11": ["^KQ11", "KQ11.KQ"],
        "^IXIC": ["^IXIC"],
        "^GSPC": ["^GSPC"],
        "^N225": ["^N225"],
        "^HSI": ["^HSI"],
    }

    tickers = fallback_map.get(ticker, [ticker])

    for t in tickers:
        try:
            df = yf.download(
                t,
                period="10d",
                interval="1d",
                progress=False,
                auto_adjust=False,
                threads=False,
            )

            if df is None or df.empty:
                continue

            df = df.dropna()
            if df.empty:
                continue

            close = df["Close"]
            if hasattr(close, "columns"):
                close = close.iloc[:, 0]

            close = close.dropna()
            if len(close) < 2:
                continue

            latest = float(close.iloc[-1])
            prev = float(close.iloc[-2])
            pct = (latest / prev - 1) * 100

            return latest, pct

        except Exception as e:
            print(f"Yahoo Finance error for {t}: {e}")

    return 0.0, 0.0


def get_us10y():
    try:
        df = yf.download(
            "^TNX",
            period="10d",
            interval="1d",
            progress=False,
            auto_adjust=False,
            threads=False,
        )

        if df is None or df.empty:
            return 0.0, 0.0

        df = df.dropna()
        close = df["Close"]

        if hasattr(close, "columns"):
            close = close.iloc[:, 0]

        close = close.dropna()

        if len(close) < 2:
            return 0.0, 0.0

        latest = float(close.iloc[-1]) / 10
        prev = float(close.iloc[-2]) / 10
        bps_change = (latest - prev) * 100

        return latest, bps_change

    except Exception as e:
        print(f"US10Y error: {e}")
        return 0.0, 0.0


def call_krx_api(endpoint, bas_dd):
    if not KRX_API_KEY:
        raise ValueError("KRX_API_KEY 환경변수가 없습니다.")

    headers = {
        "AUTH_KEY": KRX_API_KEY,
        "Content-Type": "application/json",
    }

    payload = {"basDd": bas_dd}

    res = requests.post(endpoint, headers=headers, json=payload, timeout=20)
    res.raise_for_status()
    return res.json().get("OutBlock_1", [])


def get_recent_dates(days=10):
    today = datetime.now(HK)
    return [(today - timedelta(days=i)).strftime("%Y%m%d") for i in range(days)]


def get_krx_index(index_name, endpoint):
    for bas_dd in get_recent_dates(10):
        try:
            rows = call_krx_api(endpoint, bas_dd)
        except Exception as e:
            print(f"KRX index API error for {index_name}: {e}")
            continue

        for row in rows:
            if row.get("IDX_NM") == index_name:
                close = parse_num(row.get("CLSPRC_IDX"))
                pct = parse_num(row.get("FLUC_RT"))
                return close, pct

    return 0.0, 0.0


def normalize_krx_index_name(name):
    return str(name or "").replace(" ", "").strip()


def get_krx_derivative_indices():
    for bas_dd in get_recent_dates(10):
        try:
            rows = call_krx_api(DERIVATIVE_INDEX_ENDPOINT, bas_dd)
        except Exception as e:
            print(f"KRX derivative API error: {e}")
            continue

        if not rows:
            continue

        matched = []

        for target in KRX_DERIVATIVE_INDEX_TARGETS:
            for row in rows:
                name = row.get("IDX_NM", "")
                close_raw = row.get("CLSPRC_IDX", "-")

                if name != target:
                    continue

                if close_raw in ["-", "", None]:
                    continue

                matched.append({
                    "name": name,
                    "close": parse_num(row.get("CLSPRC_IDX")),
                    "change": parse_num(row.get("CMPPREVDD_IDX")),
                    "pct": parse_num(row.get("FLUC_RT")),
                    "date": row.get("BAS_DD", bas_dd),
                })

        if matched:
            return matched

    return []


def get_krx_night_derivative_indices(strict_today=False):
    dates = [datetime.now(HK).strftime("%Y%m%d")] if strict_today else get_recent_dates(10)

    targets = []
    for target in KRX_NIGHT_DERIVATIVE_INDEX_TARGETS:
        aliases = [target["display_name"], *target.get("aliases", [])]
        targets.append({
            "display_name": target["display_name"],
            "normalized_aliases": {normalize_krx_index_name(alias) for alias in aliases},
        })

    for bas_dd in dates:
        try:
            rows = call_krx_api(DERIVATIVE_INDEX_ENDPOINT, bas_dd)
        except Exception as e:
            print(f"KRX night derivative API error: {e}")
            continue

        if not rows:
            continue

        matched_by_display_name = {}

        for row in rows:
            name = row.get("IDX_NM", "")
            close_raw = row.get("CLSPRC_IDX", "-")

            if close_raw in ["-", "", None]:
                continue

            normalized_name = normalize_krx_index_name(name)

            for target in targets:
                if normalized_name in target["normalized_aliases"]:
                    matched_by_display_name[target["display_name"]] = {
                        "name": target["display_name"],
                        "close": parse_num(row.get("CLSPRC_IDX")),
                        "change": parse_num(row.get("CMPPREVDD_IDX")),
                        "pct": parse_num(row.get("FLUC_RT")),
                        "date": row.get("BAS_DD", bas_dd),
                    }

        matched = [matched_by_display_name[t["display_name"]] for t in targets if t["display_name"] in matched_by_display_name]

        if matched:
            return matched

    return []


def format_derivative_indices(indices):
    if not indices:
        return "데이터 없음"

    lines = []
    for item in indices:
        lines.append(
            f'{html.escape(item["name"])}: '
            f'{item["close"]:,.2f} '
            f'({direction_emoji(item["pct"])} {item["pct"]:+.2f}%)'
        )

    return "\n".join(lines)


def get_weather(lat, lon):
    try:
        url = (
            "https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&current_weather=true"
        )

        res = requests.get(url, timeout=5)
        res.raise_for_status()
        data = res.json()
        weather_code = data["current_weather"]["weathercode"]

        weather_map = {
            0: "맑음 ☀️",
            1: "대체로 맑음 🌤️",
            2: "약간 흐림 ⛅",
            3: "흐림 ☁️",
            45: "안개 🌫️",
            48: "안개 🌫️",
            51: "이슬비 🌦️",
            53: "이슬비 🌦️",
            55: "이슬비 🌦️",
            61: "비 🌧️",
            63: "비 🌧️",
            65: "강한 비 🌧️",
            71: "눈 ❄️",
            73: "눈 ❄️",
            75: "강한 눈 ❄️",
            80: "소나기 🌦️",
            81: "소나기 🌦️",
            82: "강한 소나기 🌧️",
            95: "천둥번개 ⛈️",
        }

        return weather_map.get(weather_code, "날씨 정보 없음")

    except Exception as e:
        print(f"Weather error: {e}")
        return "날씨 정보 없음"


def get_today_string():
    now = datetime.now(HK)
    return now.strftime("%B %d, %Y (%A)")


def get_investor_flow(market):
    today = datetime.now(HK).strftime("%Y%m%d")

    try:
        df = stock.get_market_trading_value_by_date(today, today, market)
    except TypeError:
        df = stock.get_market_trading_value_by_date(today, today, market=market)
    except Exception as e:
        print(f"pykrx investor flow error for {market}: {e}")
        return {"개인": 0, "기관": 0, "외국인": 0}

    if df is None or df.empty:
        return {"개인": 0, "기관": 0, "외국인": 0}

    row = df.iloc[-1]

    return {
        "개인": row.get("개인", 0),
        "기관": row.get("기관합계", row.get("기관", 0)),
        "외국인": row.get("외국인합계", row.get("외국인", 0)),
    }


def morning_report():
    nasdaq, nasdaq_pct = get_yf_close_pct("^IXIC")
    spx, spx_pct = get_yf_close_pct("^GSPC")
    us10y, us10y_bps = get_us10y()

    night_indices = get_krx_night_derivative_indices()
    night_text = format_derivative_indices(night_indices)

    today_str = get_today_string()
    seoul_weather = get_weather(37.5665, 126.9780)
    hk_weather = get_weather(22.3193, 114.1694)

    msg = f"""
Good Morning Junsuk!

<b>HERE IS YOUR ☀️MORNING REPORT☀️</b>

<b><i>{today_str}</i></b>

<b><i>서울: {seoul_weather}</i></b>
<b><i>홍콩: {hk_weather}</i></b>

<b><i>오늘 하루도 열심히 합시다!</i></b>

NASDAQ 전일 종가: {nasdaq:,.2f} ({direction_emoji(nasdaq_pct)} {nasdaq_pct:+.2f}%)
S&amp;P500 전일 종가: {spx:,.2f} ({direction_emoji(spx_pct)} {spx_pct:+.2f}%)
US10Y Yield: {us10y:.3f}% ({direction_emoji(us10y_bps)} {us10y_bps:+.1f}bps)

<b>KRX 야간 선물지수</b>
{night_text}
""".strip()

    send_telegram(msg)


def afternoon_report():
    kospi, kospi_pct = get_yf_close_pct("^KS11")
    kosdaq, kosdaq_pct = get_yf_close_pct("^KQ11")

    kospi_flow = get_investor_flow("KOSPI")
    kosdaq_flow = get_investor_flow("KOSDAQ")
    save_flow_snapshot(kospi_flow, kosdaq_flow)

    derivative_indices = get_krx_derivative_indices()
    derivative_text = format_derivative_indices(derivative_indices)

    nikkei, nikkei_pct = get_yf_close_pct("^N225")
    hsi, hsi_pct = get_yf_close_pct("^HSI")

    today_str = get_today_string()
    seoul_weather = get_weather(37.5665, 126.9780)
    hk_weather = get_weather(22.3193, 114.1694)

    msg = f"""
Good Afternoon Junsuk!

<b>HERE IS YOUR ☀️AFTERNOON REPORT☀️</b>

<b><i>{today_str}</i></b>

<b><i>서울: {seoul_weather}</i></b>
<b><i>홍콩: {hk_weather}</i></b>

KOSPI 종가: {kospi:,.2f} ({direction_emoji(kospi_pct)} {kospi_pct:+.2f}%)
KOSDAQ 종가: {kosdaq:,.2f} ({direction_emoji(kosdaq_pct)} {kosdaq_pct:+.2f}%)

<b>KOSPI 순매수</b>
개인: {fmt_amount(kospi_flow["개인"])}
기관: {fmt_amount(kospi_flow["기관"])}
외국인: {fmt_amount(kospi_flow["외국인"])}

<b>KOSDAQ 순매수</b>
개인: {fmt_amount(kosdaq_flow["개인"])}
기관: {fmt_amount(kosdaq_flow["기관"])}
외국인: {fmt_amount(kosdaq_flow["외국인"])}

<b>KRX 지수 파생상품</b>
{derivative_text}

NIKKEI225 종가: {nikkei:,.2f} ({direction_emoji(nikkei_pct)} {nikkei_pct:+.2f}%)
HSI 종가: {hsi:,.2f} ({direction_emoji(hsi_pct)} {hsi_pct:+.2f}%)
""".strip()

    send_telegram(msg)


def evening_report():
    kospi, kospi_pct = get_yf_close_pct("^KS11")
    kosdaq, kosdaq_pct = get_yf_close_pct("^KQ11")

    final_kospi_flow = get_investor_flow("KOSPI")
    final_kosdaq_flow = get_investor_flow("KOSDAQ")
    snapshot = load_flow_snapshot()

    today_str = get_today_string()
    seoul_weather = get_weather(37.5665, 126.9780)
    hk_weather = get_weather(22.3193, 114.1694)
    today_key = datetime.now(HK).strftime("%Y%m%d")

    if snapshot is None:
        gap_text = "비교용 Afternoon snapshot 데이터 없음"
    elif snapshot.get("date") != today_key:
        gap_text = "비교용 Afternoon snapshot이 오늘 데이터가 아님"
    else:
        prev_kospi = snapshot["kospi_flow"]
        prev_kosdaq = snapshot["kosdaq_flow"]

        gap_text = f"""
<b>수급 괴리 vs Afternoon Report</b>

KOSPI 개인: {fmt_flow_gap(final_kospi_flow["개인"], prev_kospi["개인"])}
KOSPI 기관: {fmt_flow_gap(final_kospi_flow["기관"], prev_kospi["기관"])}
KOSPI 외국인: {fmt_flow_gap(final_kospi_flow["외국인"], prev_kospi["외국인"])}

KOSDAQ 개인: {fmt_flow_gap(final_kosdaq_flow["개인"], prev_kosdaq["개인"])}
KOSDAQ 기관: {fmt_flow_gap(final_kosdaq_flow["기관"], prev_kosdaq["기관"])}
KOSDAQ 외국인: {fmt_flow_gap(final_kosdaq_flow["외국인"], prev_kosdaq["외국인"])}
""".strip()

    msg = f"""
Good Evening Junsuk!

<b>HERE IS YOUR 🌙EVENING FLOW UPDATE🌙</b>

<b><i>{today_str}</i></b>

<b><i>서울: {seoul_weather}</i></b>
<b><i>홍콩: {hk_weather}</i></b>

<b><i>KRX 마감 이후 조정된 최종 수급 데이터입니다.</i></b>

KOSPI 종가: {kospi:,.2f} ({direction_emoji(kospi_pct)} {kospi_pct:+.2f}%)
KOSDAQ 종가: {kosdaq:,.2f} ({direction_emoji(kosdaq_pct)} {kosdaq_pct:+.2f}%)

<b>KOSPI 최종 순매수</b>
개인: {fmt_amount(final_kospi_flow["개인"])}
기관: {fmt_amount(final_kospi_flow["기관"])}
외국인: {fmt_amount(final_kospi_flow["외국인"])}

<b>KOSDAQ 최종 순매수</b>
개인: {fmt_amount(final_kosdaq_flow["개인"])}
기관: {fmt_amount(final_kosdaq_flow["기관"])}
외국인: {fmt_amount(final_kosdaq_flow["외국인"])}

{gap_text}
""".strip()

    send_telegram(msg)


def main():
    if len(sys.argv) < 2:
        raise ValueError("Use morning, afternoon, or evening")

    report_type = sys.argv[1]

    if report_type == "morning":
        morning_report()
    elif report_type == "afternoon":
        afternoon_report()
    elif report_type == "evening":
        evening_report()
    else:
        raise ValueError("Use morning, afternoon, or evening")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        error_msg = f"❌ Market bot error:\n{str(e)}"
        print(error_msg)

        try:
            send_telegram(error_msg)
        except Exception:
            pass

        raise
