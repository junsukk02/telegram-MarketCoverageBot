import os
import sys
import requests
import time
import yfinance as yf
import json
import html
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pykrx import stock
from playwright.sync_api import sync_playwright

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]
KRX_API_KEY = os.environ["KRX_API_KEY"]

HK = ZoneInfo("Asia/Hong_Kong")


KOSPI_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/kospi_dd_trd"
KOSDAQ_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/kosdaq_dd_trd"
DERIVATIVE_INDEX_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/idx/drvprod_dd_trd"


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    safe_text = text[:3900]

    res = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": safe_text,
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
        "saved_at": datetime.now(HK).strftime("%Y-%m-%d %H:%M:%S"),
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
        period="10d",
        interval="1d",
        progress=False,
        auto_adjust=False
    )

    df = df.dropna()

    close = df["Close"]

    if hasattr(close, "columns"):
        close = close.iloc[:, 0]

    latest_raw = float(close.iloc[-1])
    prev_raw = float(close.iloc[-2])

    # Yahoo ^TNX: 44.10 = 4.410%
    latest_yield = latest_raw / 10
    prev_yield = prev_raw / 10

    # yield %p change → bps
    # 4.410% - 4.390% = 0.020%p = 2bps
    bps_change = (latest_yield - prev_yield) * 100

    return latest_yield, bps_change


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
        "미국달러선물지수",
        "엔선물지수",
        "유로선물지수",
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

def get_esignal_kospi200_night_future():
    try:
        url = "https://esignal.co.kr/kospi200-futures-night/"

        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="networkidle", timeout=30000)

            page.wait_for_timeout(5000)

            price = page.locator("#dprice").inner_text(timeout=5000)
            open_price = page.locator(".opend").inner_text(timeout=5000)
            high_price = page.locator(".highd").inner_text(timeout=5000)
            low_price = page.locator(".lowd").inner_text(timeout=5000)
            prev_close = page.locator(".close1").inner_text(timeout=5000)
            volume = page.locator(".vold").inner_text(timeout=5000)
            updated_at = page.locator(".ttime").inner_text(timeout=5000)

            browser.close()

        return {
            "price": price,
            "open": open_price,
            "high": high_price,
            "low": low_price,
            "prev_close": prev_close,
            "volume": volume,
            "updated_at": updated_at,
        }

    except Exception as e:
        print(f"eSignal KOSPI200 night future error: {e}")
        return None

def format_esignal_kospi200_night_future(data):
    if data is None:
        return "데이터 없음"

    price = parse_num(data["price"])
    prev_close = parse_num(data["prev_close"])

    diff = price - prev_close
    pct = (diff / prev_close * 100) if prev_close != 0 else 0

    return (
        f'{price:,.2f} '
        f'({direction_emoji(pct)} {pct:+.2f}%)\n'
        f'고가 {data["high"]} / 저가 {data["low"]} / 거래량 {data["volume"]}'
    )

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

    try:
        df = stock.get_market_trading_value_by_date(
            today,
            today,
            market
        )

        if df.empty:
            return {
                "개인": 0.0,
                "기관": 0.0,
                "외국인": 0.0,
                "data_date": today,
                "status": "empty"
            }

        row = df.iloc[-1]

        return {
            "개인": float(row.get("개인", 0)),
            "기관": float(row.get("기관합계", row.get("기관", 0))),
            "외국인": float(row.get("외국인합계", row.get("외국인", 0))),
            "data_date": df.index[-1].strftime("%Y%m%d") if hasattr(df.index[-1], "strftime") else str(df.index[-1]),
            "status": "ok"
        }

    except Exception as e:
        print(f"Investor flow error for {market}: {e}")
        return {
            "개인": 0.0,
            "기관": 0.0,
            "외국인": 0.0,
            "data_date": today,
            "status": "error"
        }
    
def morning_report():
    nasdaq, nasdaq_pct = get_yf_close_pct("^IXIC")
    spx, spx_pct = get_yf_close_pct("^GSPC")
    us10y, us10y_bps = get_us10y()
    kospi200_night = get_esignal_kospi200_night_future()
    kospi200_night_text = format_esignal_kospi200_night_future(kospi200_night)
    
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
US10Y Yield: {us10y:.3f}% ({direction_emoji(us10y_bps)} {us10y_bps:+.1f}bps)

<b>KOSPI200 야간선물</b>
{kospi200_night_text}

""".strip()

    send_telegram(msg)
    
def afternoon_report():
    kospi, kospi_pct = get_yf_close_pct("^KS11")
    kosdaq, kosdaq_pct = get_yf_close_pct("^KQ11")

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

<b>KRX 지수 파생상품</b>
{derivative_text}

NIKKEI225 종가: {nikkei:,.2f} ({direction_emoji(nikkei_pct)} {nikkei_pct:+.2f}%)
HSI 종가: {hsi:,.2f} ({direction_emoji(hsi_pct)} {hsi_pct:+.2f}%)
""".strip()

    send_telegram(msg)


def evening_report():
    kospi, kospi_pct = get_yf_close_pct("^KS11")
    kosdaq, kosdaq_pct = get_yf_close_pct("^KQ11")

    kospi_flow = get_investor_flow("KOSPI")
    kosdaq_flow = get_investor_flow("KOSDAQ")

    today_str = get_today_string()
    seoul_weather = get_weather(37.5665, 126.9780)
    hk_weather = get_weather(22.3193, 114.1694)

    msg = f"""
Good Evening Junsuk!

<b>HERE IS YOUR 🌙EVENING FLOW UPDATE🌙</b>

<b><i>{today_str}</i></b>

<b><i>서울: {seoul_weather}</i></b>
<b><i>홍콩: {hk_weather}</i></b>

<b><i>KRX 당일 최종 순매수 거래대금입니다.</i></b>

KOSPI 종가: {kospi:,.2f} ({direction_emoji(kospi_pct)} {kospi_pct:+.2f}%)
KOSDAQ 종가: {kosdaq:,.2f} ({direction_emoji(kosdaq_pct)} {kosdaq_pct:+.2f}%)

<b>KOSPI 최종 순매수</b>
개인: {fmt_amount(kospi_flow["개인"])}
기관: {fmt_amount(kospi_flow["기관"])}
외국인: {fmt_amount(kospi_flow["외국인"])}

<b>KOSDAQ 최종 순매수</b>
개인: {fmt_amount(kosdaq_flow["개인"])}
기관: {fmt_amount(kosdaq_flow["기관"])}
외국인: {fmt_amount(kosdaq_flow["외국인"])}
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
