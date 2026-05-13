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
KOSPI_STOCK_FUTURES_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/drv/eqsfu_stk_bydd_trd"
KOSDAQ_STOCK_FUTURES_ENDPOINT = "https://data-dbg.krx.co.kr/svc/apis/drv/eqkfu_ksq_bydd_trd"


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

def get_krx_futures(endpoint, market_name="야간", limit=5):
    for bas_dd in get_recent_dates(10):
        rows = call_krx_api(endpoint, bas_dd)

        if not rows:
            continue

        futures = []

        for row in rows:
            mkt_name = row.get("MKT_NM", "")
            name = row.get("ISU_NM", "")
            close_raw = row.get("TDD_CLSPRC", "-")
            change_raw = row.get("CMPPREVDD_PRC", "-")
            volume_raw = row.get("ACC_TRDVOL", "0")
            value_raw = row.get("ACC_TRDVAL", "0")

            if mkt_name != market_name:
                continue

            if close_raw in ["-", "", None]:
                continue

            if volume_raw in ["-", "", "0", None]:
                continue

            futures.append({
                "name": name,
                "close": parse_num(close_raw),
                "change": parse_num(change_raw),
                "volume": parse_num(volume_raw),
                "trading_value": parse_num(value_raw),
            })

        if futures:
            futures = sorted(
                futures,
                key=lambda x: x["trading_value"],
                reverse=True
            )

            return futures[:limit]

    return []


def format_futures_list(futures):
    if not futures:
        return "데이터 없음"

    lines = []

    for item in futures:
        lines.append(
            f'{html.escape(item["name"])}: '
            f'{item["close"]:,.2f} '
            f'({direction_emoji(item["change"])} {item["change"]:+,.2f})'
        )

    return "\n".join(lines)

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


def get_investor_flow(market):
    today = datetime.now(HK).strftime("%Y%m%d")

    df = stock.get_market_trading_value_by_date(
        today,
        today,
        market
    )

    if df.empty:
        return {
            "개인": 0.0,
            "기관": 0.0,
            "외국인": 0.0
        }

    row = df.iloc[-1]

    return {
        "개인": float(row.get("개인", 0)),
        "기관": float(row.get("기관합계", row.get("기관", 0))),
        "외국인": float(row.get("외국인합계", row.get("외국인", 0))),
    }
    
def morning_report():
    nasdaq, nasdaq_pct = get_yf_close_pct("^IXIC")
    spx, spx_pct = get_yf_close_pct("^GSPC")
    us10y, us10y_bps = get_us10y()

    kospi_night_futures = get_krx_futures(
        KOSPI_STOCK_FUTURES_ENDPOINT,
        market_name="야간",
        limit=5
    )

    kosdaq_night_futures = get_krx_futures(
        KOSDAQ_STOCK_FUTURES_ENDPOINT,
        market_name="야간",
        limit=5
    )

    kospi_night_futures_text = format_futures_list(kospi_night_futures)
    kosdaq_night_futures_text = format_futures_list(kosdaq_night_futures)

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

<b>KRX 야간 Futures</b>

<b>KOSPI</b>
{kospi_night_futures_text}

<b>KOSDAQ</b>
{kosdaq_night_futures_text}
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

    kospi_regular_futures = get_krx_futures(
        KOSPI_STOCK_FUTURES_ENDPOINT,
        market_name="정규",
        limit=5
    )

    kosdaq_regular_futures = get_krx_futures(
        KOSDAQ_STOCK_FUTURES_ENDPOINT,
        market_name="정규",
        limit=5
    )

    kospi_regular_futures_text = format_futures_list(kospi_regular_futures)
    kosdaq_regular_futures_text = format_futures_list(kosdaq_regular_futures)

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

<b>KRX Regular Futures</b>

<b>KOSPI</b>
{kospi_regular_futures_text}

<b>KOSDAQ</b>
{kosdaq_regular_futures_text}

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

    today_key = datetime.now(HK).strftime("%Y%m%d")

    if snapshot is None:
        change_text = "비교용 Afternoon snapshot 데이터 없음"
    elif snapshot.get("date") != today_key:
        change_text = "비교용 Afternoon snapshot이 오늘 데이터가 아님"
    else:
        prev_kospi = snapshot["kospi_flow"]
        prev_kosdaq = snapshot["kosdaq_flow"]

        change_text = f"""
<b>수급 괴리 vs Afternoon Report</b>

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
