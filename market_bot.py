import os
import sys
import requests
import yfinance as yf
import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from pykrx import stock

TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
CHAT_ID = os.environ["TELEGRAM_CHAT_ID"]

HK = ZoneInfo("Asia/Hong_Kong")


def send_telegram(text):
    url = f"https://api.telegram.org/bot{TOKEN}/sendMessage"

    res = requests.post(url, json={
        "chat_id": CHAT_ID,
        "text": text,
        "parse_mode": "HTML"
    })

    res.raise_for_status()


def direction_emoji(value):
    return "🟢" if value >= 0 else "🔴"


def get_yf_close_pct(ticker):
    df = yf.download(
        ticker,
        period="7d",
        interval="1d",
        progress=False,
        auto_adjust=False
    )

    df = df.dropna()

    latest = float(df["Close"].iloc[-1])
    prev = float(df["Close"].iloc[-2])
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

    latest = float(df["Close"].iloc[-1]) / 10
    prev = float(df["Close"].iloc[-2]) / 10

    bps_change = (latest - prev) * 100

    return latest, bps_change

def get_weather(lat, lon):
    url = (
        f"https://api.open-meteo.com/v1/forecast"
        f"?latitude={lat}&longitude={lon}&current_weather=true"
    )

    res = requests.get(url)
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

def get_today_string():
    now = datetime.now(HK)

    return now.strftime("%B %d, %Y (%A)")

def morning_report():
    nasdaq, nasdaq_pct = get_yf_close_pct("^IXIC")
    spx, spx_pct = get_yf_close_pct("^GSPC")
    us10y, us10y_bps = get_us10y()

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
""".strip()

    send_telegram(msg)


def get_kr_index(index_code):
    today = datetime.now(HK).strftime("%Y%m%d")
    start = (datetime.now(HK) - timedelta(days=14)).strftime("%Y%m%d")

    df = stock.get_index_ohlcv_by_date(start, today, index_code)
    df = df.dropna()

    latest = float(df.iloc[-1]["종가"])
    prev = float(df.iloc[-2]["종가"])
    pct = (latest / prev - 1) * 100

    return latest, pct


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


def fmt_amount(value):
    value = float(value)

    abs_value = abs(value)

    if abs_value >= 1_000_000_000:
        formatted = f"{value / 1_000_000_000:+.2f}bn"
    elif abs_value >= 1_000_000:
        formatted = f"{value / 1_000_000:+.2f}mn"
    elif abs_value >= 1_000:
        formatted = f"{value / 1_000:+.2f}k"
    else:
        formatted = f"{value:+.0f}"

    return formatted


def afternoon_report():
    kospi, kospi_pct = get_kr_index("1001")
    kosdaq, kosdaq_pct = get_kr_index("2001")

    kospi_flow = get_investor_flow("KOSPI")
    kosdaq_flow = get_investor_flow("KOSDAQ")

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


if __name__ == "__main__":
    report_type = sys.argv[1]

    if report_type == "morning":
        morning_report()
    elif report_type == "afternoon":
        afternoon_report()
    else:
        raise ValueError("Use morning or afternoon")
