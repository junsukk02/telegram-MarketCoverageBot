diff --git a/market_bot.py b/market_bot.py
index b80dab3e028578515195aafcb5427948c9222e62..197b60ef5658b739346212aedb168e7afafa6878 100644
--- a/market_bot.py
+++ b/market_bot.py
@@ -1,48 +1,67 @@
 import os
 import sys
 import requests
-import time
 import yfinance as yf
 import json
 import html
-from playwright.sync_api import sync_playwright
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
 
+KRX_NIGHT_DERIVATIVE_INDEX_TARGETS = [
+    {
+        "api_name": "코스피 200 선물지수 (야간)",
+        "display_name": "코스피200선물지수 (야간)",
+        "aliases": [
+            "코스피 200 선물지수 (야간)",
+            "코스피200선물지수 (야간)",
+            "코스피200 야간선물지수",
+        ],
+    },
+    {
+        "api_name": "코스닥 150 선물지수 (야간)",
+        "display_name": "코스닥150선물지수 (야간)",
+        "aliases": [
+            "코스닥 150 선물지수 (야간)",
+            "코스닥150선물지수 (야간)",
+            "코스닥150 야간선물지수",
+        ],
+    },
+]
+
 
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
@@ -256,151 +275,120 @@ def get_krx_derivative_indices():
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
 
 
-def format_derivative_indices(indices):
-    if not indices:
-        return "데이터 없음"
 
-    lines = []
-
-    for item in indices:
-        lines.append(
-            f'{html.escape(item["name"])}: '
-            f'{item["close"]:,.2f} '
-            f'({direction_emoji(item["pct"])} {item["pct"]:+.2f}%)'
-        )
+def normalize_krx_index_name(name):
+    return str(name or "").replace(" ", "").strip()
 
-    return "\n".join(lines)
 
-def get_esignal_kospi200_night_future():
-    try:
-        url = "https://esignal.co.kr/kospi200-futures-night/"
+def get_krx_night_derivative_indices(strict_today=False):
+    dates = [datetime.now(HK).strftime("%Y%m%d")] if strict_today else get_recent_dates(10)
+    targets = []
 
-        captured = {}
+    for target in KRX_NIGHT_DERIVATIVE_INDEX_TARGETS:
+        aliases = [target["api_name"], target["display_name"], *target.get("aliases", [])]
+        targets.append({
+            "api_name": target["api_name"],
+            "display_name": target["display_name"],
+            "normalized_aliases": {normalize_krx_index_name(alias) for alias in aliases},
+        })
 
-        def handle_response(response):
-            try:
-                if "socket.io" not in response.url:
-                    return
-
-                body = response.text()
-
-                if '42["populate"' not in body:
-                    return
-
-                import re
-
-                match = re.search(r'42\["populate","(.+?)"\]', body)
-
-                if not match:
-                    return
-
-                raw_json = match.group(1)
-                raw_json = raw_json.encode().decode("unicode_escape")
-
-                data = json.loads(raw_json)
-
-                captured["data"] = data
-
-            except Exception:
-                pass
+    for bas_dd in dates:
+        rows = call_krx_api(DERIVATIVE_INDEX_ENDPOINT, bas_dd)
 
-        with sync_playwright() as p:
-            browser = p.chromium.launch(headless=True)
-            page = browser.new_page(
-                user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
-            )
+        if not rows:
+            continue
 
-            page.on("response", handle_response)
+        matched_by_display_name = {}
 
-            page.goto(url, wait_until="domcontentloaded", timeout=30000)
+        for row in rows:
+            name = row.get("IDX_NM", "")
+            normalized_name = normalize_krx_index_name(name)
+            close_raw = row.get("CLSPRC_IDX", "-")
 
-            for _ in range(10):
-                if "data" in captured:
-                    break
-                page.wait_for_timeout(1000)
+            if close_raw in ["-", "", None]:
+                continue
 
-            browser.close()
+            for target in targets:
+                if normalized_name not in target["normalized_aliases"]:
+                    continue
 
-        if "data" not in captured:
-            return None
+                matched_by_display_name[target["display_name"]] = {
+                    "name": target["display_name"],
+                    "api_name": name,
+                    "close": parse_num(row.get("CLSPRC_IDX")),
+                    "change": parse_num(row.get("CMPPREVDD_IDX")),
+                    "pct": parse_num(row.get("FLUC_RT")),
+                    "date": row.get("BAS_DD", bas_dd),
+                }
 
-        data = captured["data"]
+        if matched_by_display_name:
+            return [
+                matched_by_display_name[target["display_name"]]
+                for target in targets
+                if target["display_name"] in matched_by_display_name
+            ]
 
-        return {
-            "price": data.get("value", "0"),
-            "diff": data.get("value_diff", "0"),
-            "prev_close": data.get("value_day", "0"),
-            "open": data.get("open", "0"),
-            "high": data.get("high", "0"),
-            "low": data.get("low", "0"),
-            "volume": data.get("volume", 0),
-            "updated_at": data.get("tstamp", ""),
-        }
+    return []
 
-    except Exception as e:
-        print(f"eSignal KOSPI200 night future error: {e}")
-        return None
-        
-def format_esignal_kospi200_night_future(data):
-    if data is None:
+def format_derivative_indices(indices):
+    if not indices:
         return "데이터 없음"
 
-    price = parse_num(data["price"])
-    diff = parse_num(data["diff"])
-    prev_close = parse_num(data["prev_close"])
+    lines = []
 
-    pct = (diff / prev_close * 100) if prev_close != 0 else 0
+    for item in indices:
+        lines.append(
+            f'{html.escape(item["name"])}: '
+            f'{item["close"]:,.2f} '
+            f'({direction_emoji(item["pct"])} {item["pct"]:+.2f}%)'
+        )
+
+    return "\n".join(lines)
 
-    return (
-        f'{price:,.2f} '
-        f'({direction_emoji(diff)} {pct:+.2f}%)\n'
-        f'고가 {data["high"]} / 저가 {data["low"]} / 거래량 {int(data["volume"]):,}'
-    )
-    
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
@@ -438,75 +426,75 @@ def get_investor_flow(market):
 
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
-    kospi200_night = get_esignal_kospi200_night_future()
-    kospi200_night_text = format_esignal_kospi200_night_future(kospi200_night)
+    night_derivative_indices = get_krx_night_derivative_indices()
+    night_derivative_text = format_derivative_indices(night_derivative_indices)
     
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
 
-<b>KOSPI200 야간선물</b>
-{kospi200_night_text}
+<b>KRX 야간 선물지수</b>
+{night_derivative_text}
 
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
