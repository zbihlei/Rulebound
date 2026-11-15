#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""v2.1.1: автозбереження в GitHub прибрано з додатка — немає налаштування git_autopush, розділу «GitHub»
у «Налаштуваннях», маршрутів /api/git/*; старий settings.json з git_autopush читається без помилок.
Запуск: python3 tests/test_v211.py"""
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
APP = os.path.dirname(HERE)
sys.path.insert(0, APP)
for k in ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy"):
    os.environ.pop(k, None)
os.environ["NO_PROXY"] = os.environ["no_proxy"] = "127.0.0.1,localhost"
ok = True


def check(name, cond, info=""):
    global ok
    print(("OK   " if cond else "FAIL ") + name + ("" if cond or not info else f"  {info}"))
    ok = ok and bool(cond)


data = tempfile.mkdtemp(prefix="smc211_")
with open(os.path.join(data, "settings.json"), "w", encoding="utf-8") as f:   # старий файл — ще з git_autopush
    json.dump({"git_autopush": True, "spot_auto": False, "caffeinate": False, "ltf_enabled": False, "tg_enabled": False}, f)
os.environ["SMC_APP_DATA"] = data
import app  # noqa: E402

check("немає налаштування git_autopush і прапорця для нього",
      "git_autopush" not in app.DEFAULTS and "git_autopush" not in app.FLAGS and not hasattr(app, "GIT"))
s = app.load_settings()
check("старий settings.json з git_autopush читається, зайвий ключ ігнорується", "git_autopush" not in s and s["spot_auto"] is False)
check("модуля gitsync більше немає", not os.path.exists(os.path.join(APP, "gitsync.py")) and "gitsync" not in sys.modules)

PORT = 8771
URL = f"http://127.0.0.1:{PORT}"
srv = subprocess.Popen([sys.executable, os.path.join(APP, "app.py"), "--port", str(PORT), "--no-browser"],
                       env=dict(os.environ, SMC_APP_DATA=data, SMC_TG_TOKEN=""), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def code_of(path, body=None):
    req = urllib.request.Request(URL + path, data=None if body is None else json.dumps(body).encode(),
                                 headers={"X-SMC-App": "1", "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return r.status, r.read().decode("utf-8")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


try:
    for _ in range(80):
        try:
            urllib.request.urlopen(URL + "/api/status", timeout=1)
            break
        except Exception:
            time.sleep(0.1)
    c, html = code_of("/")
    check("у «Налаштуваннях» немає розділу «GitHub» і кнопки «Запушити зараз»",
          c == 200 and "<legend>GitHub</legend>" not in html and "gitPush" not in html and 'name="git_autopush"' not in html)
    c, js = code_of("/app.js")
    check("в інтерфейсі немає коду автозбереження", c == 200 and "/api/git" not in js and "loadGit" not in js)
    check("GET /api/git/status — немає", code_of("/api/git/status")[0] == 404)
    check("POST /api/git/push — немає", code_of("/api/git/push", {})[0] == 404)
    c, st = code_of("/api/settings")
    check("/api/settings працює й не віддає git_autopush", c == 200 and "git_autopush" not in json.loads(st)["settings"])
finally:
    srv.terminate()

print("\nПІДСУМОК: " + ("усі перевірки пройдено" if ok else "Є ПОМИЛКИ"))
sys.exit(0 if ok else 1)
