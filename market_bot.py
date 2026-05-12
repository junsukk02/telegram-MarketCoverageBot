import os
import sys
import requests
import yfinance as yf
import json
import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pykrx import stock

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
KRX_API_KEY = os.environ["KRX_API_KEY"]

HK = ZoneInfo("Asia/Hong_Kong")


KOSPI_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd"
KOSDAQ_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/kosdaq_dd_trd"
DERIVATIVE_INDEX_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/drvprod_dd_trd"


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    res = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML",
        "disable_web_page_preview": True
    })

    if not res.ok:
        print("Telegram error:", res.status_code, res.text)

    res.raise_for_status()


def direction_emoji(value):
    return "🟢" if value >= 0 else "🔴"


def parse_num(value):
    if value is None:
        return 0.0

    value = str(value).replace(",", "").replace("%", "").strip()

    if value in ["", "-", "nan"]:
        return 0.0

    return float(value)


def fmt_amount(value):
    value = float(value)

    emoji = "🟢" if value >= 0 else "🔴"

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

SNAPSHOT_FILE = "flow_snapshot.json"


def save_flow_snapshot(kospi_flow, kosdaq_flow):
    snapshot = {
        "date": datetime.now(HK).strftime("%Y%m%d"),
        "kospi_flow": kospi_flow,
        "kosdaq_flow": kosdaq_flow,
    }

    with open(SNAPSHOT_FILE, "w", encoding="utf-8") as f:
        json.dump(snapshot, f, ensure_ascii=False, indent=2)


def load_flow_snapshot():
    if not os.path.exists(SNAPSHOT_FILE):
        return None

    with open(SNAPSHOT_FILE, "r", encoding="utf-8") as f:
        return json.load(f)

def get_yf_close_pct(ticker):
    df = yf.download(
        ticker,
        period="7d",
        interval="1d",
        progress=False,
        auto_adjust=False
    )

    df = df.dropna()

    close = df["Close"]

    if hasattr(close, "columns"):
        close = close.iloc[:, 0]

    latest = float(close.iloc[-1])
    prev = float(close.iloc[-2])

    pct = (latest / prev - 1) * 100

    return latest, pct


def get_us10y():
    df = yf.download(
        "^TNX",
        period="7d",
        interval="1d",
        progress=False,
        auto_adjust=False
    )

    df = df.dropna()

    close = df["Close"]

    if hasattr(close, "columns"):
        close = close.iloc[:, 0]

    latest = float(close.iloc[-1]) / 10
    prev = float(close.iloc[-2]) / 10

    bps_change = (latest - prev) * 100

    return latest, bps_change


def call_krx_api(endpoint, bas_dd):
    headers = {
        "AUTH_KEY": KRX_API_KEY,
        "Content-Type": "application/json"
    }

    payload = {
        "basDd": bas_dd
    }

    res = requests.post(
        endpoint,
        headers=headers,
        json=payload,
        timeout=20
    )

    res.raise_for_status()
    return res.json().get("OutBlock_1", [])

def get_recent_dates(days=10):
    today = datetime.now(HK)

    dates = []
    for i in range(days):
        d = today - timedelta(days=i)
        dates.append(d.strftime("%Y%m%d"))

    return dates

def get_krx_index(index_name, endpoint):
    for bas_dd in get_recent_dates(10):
        rows = call_krx_api(endpoint, bas_dd)

        if not rows:
            continue

        for row in rows:
            if row.get("IDX_NM") == index_name:
                close = parse_num(row.get("CLSPRC_IDX"))
                pct = parse_num(row.get("FLUC_RT"))
                return close, pct

    raise ValueError(f"{index_name} 데이터를 최근 10일 내에서 찾을 수 없습니다.")

def get_krx_derivative_indices():
    target_names = [
        "코스피 200 선물지수",
        "코스닥 150 선물지수",
    ]

    for bas_dd in get_recent_dates(10):
        rows = call_krx_api(DERIVATIVE_INDEX_ENDPOINT, bas_dd)

        if not rows:
            continue

        matched = []

        for target in target_names:
            for row in rows:
                name = row.get("IDX_NM", "")

                if name == target:
                    matched.append({
                        "name": name,
                        "close": parse_num(row.get("CLSPRC_IDX")),
                        "change": parse_num(row.get("CMPPREVDD_IDX")),
                        "pct": parse_num(row.get("FLUC_RT")),
                    })

        if matched:
            return matched

    return []


def get_weather(lat, lon):
    try:
        url = (
            f"https://api.open-meteo.com/v1/forecast"
            f"?latitude={lat}&longitude={lon}&current_weather=true"
        )

        res = requests.get(url, timeout=3)
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
            61: "비 🌧️",
            63: "비 🌧️",
            65: "강한 비 🌧️",
            71: "눈 ❄️",
            80: "소나기 🌦️",
            95: "천둥번개 ⛈️",
        }

        return weather_map.get(weather_code, "날씨 정보 없음")

    except Exception:
        return "날씨 정보 없음"


def get_today_string():
    now = datetime.now(HK)
    return now.strftime("%B %d, %Y (%A)")


def get_investor_flow(market):
    today = datetime.now(HK).strftime("%Y%m%d")

    df = stock.get_market_trading_value_by_date(
        today,
        today,
        market
    )

    if df.empty:
        return {
            "개인": 0,
            "기관": 0,
            "외국인": 0
        }

    row = df.iloc[-1]

    return {
        "개인": row.get("개인", 0),
        "기관": row.get("기관합계", row.get("기관", 0)),
        "외국인": row.get("외국인합계", row.get("외국인", 0)),
    }


def get_krx_derivative_indices():
    for bas_dd in get_recent_dates(10):
        rows = call_krx_api(DERIVATIVE_INDEX_ENDPOINT, bas_dd)

        if not rows:
            continue

        indices = []

        for row in rows:
            name = row.get("IDX_NM", "")

            if not name:
                continue

            indices.append({
                "name": name,
                "close": parse_num(row.get("CLSPRC_IDX")),
                "change": parse_num(row.get("CMPPREVDD_IDX")),
                "pct": parse_num(row.get("FLUC_RT")),
            })

        if indices:
            return indices

    return []


def morning_report():
    nasdaq, nasdaq_pct = get_yf_close_pct("^IXIC")
    spx, spx_pct = get_yf_close_pct("^GSPC")
    us10y, us10y_bps = get_us10y()

    derivative_indices = get_krx_derivative_indices()

    derivative_text = "\n".join([
        f'{html.escape(item["name"])}: {item["close"]:,.2f} ({direction_emoji(item["pct"])} {item["pct"]:+.2f}%)'
        for item in derivative_indices
    ]) or "데이터 없음"

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
S&P500 전일 종가: {spx:,.2f} ({direction_emoji(spx_pct)} {spx_pct:+.2f}%)
US10Y Yield: {us10y:.3f}% ({direction_emoji(us10y_bps)} {us10y_bps:+.0f}bps)

<b>KRX 파생상품지수</b>
{derivative_text}
""".strip()

    send_telegram(msg)


def afternoon_report():
    kospi, kospi_pct = get_yf_close_pct("^KS11")
    kosdaq, kosdaq_pct = get_yf_close_pct("^KQ11")

    kospi_flow = get_investor_flow("KOSPI")
    kosdaq_flow = get_investor_flow("KOSDAQ")
    
    save_flow_snapshot(kospi_flow, kosdaq_flow)

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

    if snapshot is None:
        change_text = "비교용 afternoon snapshot 데이터 없음"
    else:
        prev_kospi = snapshot["kospi_flow"]
        prev_kosdaq = snapshot["kosdaq_flow"]

        change_text = f"""
<b>수급 변화 vs Afternoon Report</b>

KOSPI 개인: {fmt_amount(final_kospi_flow["개인"] - prev_kospi["개인"])}
KOSPI 기관: {fmt_amount(final_kospi_flow["기관"] - prev_kospi["기관"])}
KOSPI 외국인: {fmt_amount(final_kospi_flow["외국인"] - prev_kospi["외국인"])}

KOSDAQ 개인: {fmt_amount(final_kosdaq_flow["개인"] - prev_kosdaq["개인"])}
KOSDAQ 기관: {fmt_amount(final_kosdaq_flow["기관"] - prev_kosdaq["기관"])}
KOSDAQ 외국인: {fmt_amount(final_kosdaq_flow["외국인"] - prev_kosdaq["외국인"])}
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

{change_text}
""".strip()

    send_telegram(msg)

if __name__ == "__main__":
    try:
        report_type = sys.argv[1]
        
        if report_type == "morning":
            morning_report()
            
        elif report_type == "afternoon":
            afternoon_report()
            
        elif report_type == "evening":
            evening_report()

        else:
            raise ValueError(
                "Use morning, afternoon, or evening"
            )

    except Exception as e:
        error_msg = f"❌ Market bot error:\n{str(e)}"

        print(error_msg)

        try:
            send_telegram(error_msg)
        except:
            pass

        raise
