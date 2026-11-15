#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.2.0: фідбек на сигнали — оцінка (мій сетап / не мій / неактуально) з причиною, лог ознак сигналу,
«сховати зону»: без алертів (повний скан і живий), без уточнення 15m, окремий перемикач «Сховані» в інтерфейсі.
Запуск: python3 tests/test_v220.py [папка_скріншотів]"""
import csv
import datetime as dt
import json
import os
import subprocess
import sys
import tempfile
import time
import types
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
shots = sys.argv[1] if len(sys.argv) > 1 else tempfile.mkdtemp()
os.makedirs(shots, exist_ok=True)

import feedback as FB  # noqa: E402
import screener_runner as SR  # noqa: E402
import smc_screener as SC  # noqa: E402

ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


def row(sym, d="Long", zl=1.2345678912, zh=1.3, rank=1, status="підхід до зони"):
    return {"symbol": sym, "dir": d, "zone_low": zl, "zone_high": zh, "status": status, "rank": rank, "score": 2,
            "price": zh * 1.02, "entry": (zl + zh) / 2, "stop": zl * 0.98, "target": zh * 1.2, "rr": 3.4, "rr_mid": 3.4,
            "dist": 1.5, "fib": 0.62, "fib_ok": True, "fvg": 1, "sweep": "свіп мінімуму", "vol_conf": 2, "volume": "імпульс ×1.6 ✓",
            "atr_pct": 2.1, "knife": False, "struct": 1, "against": False, "zone_type": "OB", "bos_time": "14.11 08:00",
            "bos_bars_ago": 7, "flags": ["тісний стоп: 0.9%", "стоп за гнотом"], "chart": [[1, 2, 3, 0.5, 2.5]]}


# ================================================================ 1. модуль feedback
d = tempfile.mkdtemp(prefix="smc220_")
a, b, c = row("AAAUSDT"), row("BBBUSDT", "Short", 50.5, 51.0), row("CCCUSDT", zl=0.000012345678)
check("ключ зони — той самий формат, що live_key скринера і _key обгортки",
      FB.row_key(a) == SC.live_key(a) == SR._key(a) and FB.row_key(c) == SC.live_key(c), (FB.row_key(a), SC.live_key(a)))
k = FB.record(d, a, "like")
FB.record(d, b, "dislike", "knife", "  забагато   гнотів ", mute=True)
FB.record(d, c, "stale", "late", mute=True)
with open(os.path.join(d, FB.LOG_FILE), encoding="utf-8-sig") as f:
    log = list(csv.DictReader(f))
check("лог оцінок: 3 рядки, усі ознаки сигналу на той момент", len(log) == 3 and log[0]["verdict"] == "like"
      and log[1]["reason"] == "knife" and log[1]["note"] == "забагато гнотів" and log[1]["muted"] == "так"
      and log[0]["rr"] == "3.4" and log[0]["sweep"] == "свіп мінімуму" and log[0]["flags"] == "тісний стоп: 0.9%; стоп за гнотом"
      and set(FB.FEATURES) <= set(log[0]), log[1])
check("графік у лог не пишеться", "chart" not in log[0])
last = FB.last_feedback(d)
check("остання оцінка по зоні", last[k]["verdict"] == "like" and last[FB.row_key(b)]["reason"] == "knife")
check("сховані — лише ті, де «сховати»", FB.muted_keys(d) == {FB.row_key(b), FB.row_key(c)})
check("drop_muted: лишає несховані в тому ж порядку", [r["symbol"] for r in FB.drop_muted(d, [a, b, c])] == ["AAAUSDT"])
for args, why in ((("maybe",), "невідома оцінка"), (("dislike", "astro"), "невідома причина"), (("like", "", "", True), "«мій сетап» не ховається")):
    try:
        FB.record(d, a, *args)
        check(f"відхиляється: {why}", False)
    except ValueError:
        check(f"відхиляється: {why}", True)
check("повернути зону", FB.unmute(d, FB.row_key(c)) and FB.muted_keys(d) == {FB.row_key(b)} and not FB.unmute(d, "x|y|1"))
m = FB.muted(d)
m[FB.row_key(b)]["last_seen"] = (dt.datetime.now() - dt.timedelta(days=8)).isoformat(timespec="seconds")
FB._write(os.path.join(d, FB.MUTED_FILE), m)
check("зона ще у списку → не прибирається, last_seen оновлено", FB.touch_seen(d, [b]) == 0 and FB.row_key(b) in FB.muted_keys(d))
m = FB.muted(d)
m[FB.row_key(b)]["last_seen"] = (dt.datetime.now() - dt.timedelta(days=8)).isoformat(timespec="seconds")
FB._write(os.path.join(d, FB.MUTED_FILE), m)
check("зони немає у списку понад 7 днів → прибирається сама", FB.touch_seen(d, [a]) == 1 and not FB.muted_keys(d))

# ================================================================ 2. обгортка скринера: алерти схованих зон глушаться
d2 = tempfile.mkdtemp(prefix="smc220_run_")
got = {"alert_rows": [], "fired": []}
mod = types.ModuleType("fake_screener")
mod.MKT = {"futures": True}
mod.save_json = lambda rows, path, stamp: None
mod.append_log = lambda out_dir, stamp, rows, rejects, live=None: None
mod.notify = lambda *a_, **k_: None
mod.alert_new = lambda rows, seen_path: got["alert_rows"].extend(r["symbol"] for r in rows) or 0
mod.live_key = SC.live_key
mod.live_touches = SC.live_touches
mod.fire_live = lambda a_, r, px, seen_path, stamp: got["fired"].append(r["symbol"])
SR.install_hooks(mod)
x, y = row("XXXUSDT", zl=10.0, zh=11.0), row("YYYUSDT", zl=20.0, zh=21.0)
FB.record(d2, y, "stale", "late", mute=True)
mod.alert_new([x, y], os.path.join(d2, "seen.json"))
check("повний скан: схованої зони немає серед алертів", got["alert_rows"] == ["XXXUSDT"], got["alert_rows"])
mod.fetch_prices = lambda: {"XXXUSDT": 10.5, "YYYUSDT": 20.5}   # ціна зайшла в обидві зони
SR.STATE["scan_failed"] = False
SR.make_live_wait(mod)(types.SimpleNamespace(watch=0.03, price_check=0.2, out=d2), [x, y], os.path.join(d2, "seen.json"))
check("живий алерт «ціна в зоні»: лише для несхованої зони", got["fired"] == ["XXXUSDT"], got["fired"])

# ================================================================ 3. сервер + інтерфейс
from playwright.sync_api import sync_playwright, expect  # noqa: E402

PORT = 8769
URL = f"http://127.0.0.1:{PORT}"
data = tempfile.mkdtemp(prefix="smc220_app_")
res_dir = tempfile.mkdtemp(prefix="smc220_res_")
with open(os.path.join(data, "settings.json"), "w") as f:
    json.dump({"screener_path": os.path.join(HERE, "fake_screener.py"), "results_dir": res_dir, "spot_auto": False,
               "ltf_enabled": False, "backup_extra_dir": "", "tg_enabled": False}, f)
cands = [row("AAAUSDT"), row("BBBUSDT", "Short", 50.5, 51.0, rank=0, status="у зоні (1-й дотик)")]
with open(os.path.join(res_dir, "latest_app.json"), "w", encoding="utf-8") as f:
    json.dump({"generated": "12:00", "written_at": dt.datetime.now().astimezone().isoformat(), "market": "futures",
               "count": 2, "candidates": cands}, f, ensure_ascii=False)
app = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=data, SMC_TG_TOKEN=""), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
for _ in range(80):
    try:
        urllib.request.urlopen(URL + "/api/status", timeout=1)
        break
    except Exception:
        time.sleep(0.1)


def post(path, body):
    req = urllib.request.Request(URL + path, data=json.dumps(body).encode(), headers={"X-SMC-App": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=10) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read())


try:
    c1, r1 = post("/api/feedback", {"symbol": "ZZZUSDT", "dir": "Long", "zone_low": 1, "verdict": "like"})
    check("API: сигналу немає в останньому скані → 404 з поясненням", c1 == 404 and "немає" in r1["error"], r1)
    c2, r2 = post("/api/feedback", {"symbol": "AAAUSDT", "dir": "Long", "zone_low": 1.2345678912, "verdict": "dislike", "reason": "astro"})
    check("API: невідома причина → 400", c2 == 400, r2)
    with sync_playwright() as pw:
        b_ = pw.chromium.launch()
        pg = b_.new_page(viewport={"width": 1400, "height": 1000})
        errors = []
        pg.add_init_script("window.SMC_NO_WS = true; localStorage.setItem('smc_view_mode', 'tiles');")
        pg.on("pageerror", lambda e: errors.append(str(e)))
        pg.goto(URL)
        expect(pg.locator("#charts .card")).to_have_count(2, timeout=15000)
        check("на кожній картці — рядок «Твоя оцінка» з трьома кнопками", pg.locator("#charts .fbrow").count() == 2
              and pg.locator("#charts [data-fbv]").count() == 6)
        check("перемикача «Сховані» не видно, поки нічого не сховано", pg.locator("#mutedChips").is_hidden())
        check("на кожному блоці вгорі — три кнопки оцінки", pg.locator("#tiles .tile .t-fbbar").count() == 2
              and pg.locator("#tiles [data-fbv]").count() == 6)
        pg.locator("#tiles .tile:has-text('AAA') [data-fbv=like]").click()
        expect(pg.locator("#msg")).to_contain_text("мій сетап")
        expect(pg.locator("#charts .card:has-text('AAAUSDT') [data-fbv=like]")).to_have_class("chip small on", timeout=8000)
        check("👍 з блока — одним кліком: підсвічено і на блоці, і на картці, біля назви — позначка",
              pg.locator("#tiles .tile:has-text('AAA') .t-fbb.on").count() == 1 and pg.locator("#tiles .tile:has-text('AAA') .t-fb").count() == 1)
        card_b = pg.locator("#charts .card:has-text('BBBUSDT')")
        pg.locator("#tiles .tile:has-text('BBB') [data-fbv=dislike]").click()
        form = card_b.locator(".fbform")
        expect(form).to_be_visible()
        check("👎 з блока — прокрутка до картки сигналу й вибір причини там", form.is_visible())
        check("👎 відкриває вибір причини; «сховати» за замовчуванням вимкнено", form.locator(".fbr").count() == len(FB.REASONS)
              and not form.locator("input[type=checkbox]").is_checked())
        form.locator("[data-fbsave]").click()
        expect(form.locator("span.small")).to_contain_text("Вибери причину")
        check("без причини не зберігається — підказка", True)
        card_b.locator("[data-fbv=stale]").click()
        check("⊘ «неактуально»: «сховати» за замовчуванням увімкнено", form.locator("input[type=checkbox]").is_checked())
        form.locator("input[value=late]").check()
        form.locator(".fbnote").fill("вже відпрацювала вчора")
        form.locator("[data-fbsave]").click()
        expect(pg.locator("#charts .card")).to_have_count(1, timeout=8000)
        check("сховану зону прибрано зі списку, перемикач «Сховані 1»", pg.locator("#mutedChips").is_visible()
              and pg.inner_text("#cMuted") == "1" and pg.inner_text("#cAll") == "1")
        check("банер «Ціна вже в зоні» без схованої зони", "BBB" not in (pg.inner_text("#zoneAlert") if pg.locator("#zoneAlert").is_visible() else ""))
        mu = json.load(open(os.path.join(res_dir, FB.MUTED_FILE), encoding="utf-8"))
        with open(os.path.join(res_dir, FB.LOG_FILE), encoding="utf-8-sig") as f:
            lg = list(csv.DictReader(f))
        check("файли: muted.json і лог з причиною й коментарем", list(mu) == ["BBBUSDT|Short|50.5"] and lg[-1]["verdict"] == "stale"
              and lg[-1]["note"] == "вже відпрацювала вчора" and lg[-1]["market"] == "crypto", (mu, lg[-1]))
        pg.click("#mutedChip")
        expect(pg.locator("#charts .card")).to_have_count(1)
        check("«Сховані»: показано лише сховану, з кнопкою «повернути»", "BBBUSDT" in pg.inner_text("#charts")
              and pg.locator("#charts [data-unmute]").count() == 1)
        pg.click("#charts [data-unmute]")
        expect(pg.locator("#msg")).to_contain_text("повернуто", timeout=8000)
        expect(pg.locator("#charts .card")).to_have_count(2, timeout=8000)
        check("повернуто останню сховану: список сам повертається до звичайного, обидві картки, файл схованих порожній",
              json.load(open(os.path.join(res_dir, FB.MUTED_FILE), encoding="utf-8")) == {} and pg.locator("#mutedChips").is_hidden())
        pg.set_viewport_size({"width": 390, "height": 844})
        pg.wait_for_timeout(300)
        wide = pg.evaluate("[...document.querySelectorAll('.fbrow, .fbrow *, .fbform, .fbform *')].filter(e => e.getBoundingClientRect().right > 392).slice(0, 4).map(e => e.tagName + '.' + e.className)")
        pg.screenshot(path=os.path.join(shots, "v220_narrow.png"))
        check("вузьке вікно: рядок оцінки не вилазить за екран", not wide, wide)
        check("помилок JS немає", not errors, errors[:3])
        b_.close()
finally:
    app.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"), "| скріншоти:", shots)
sys.exit(0 if ok else 1)
