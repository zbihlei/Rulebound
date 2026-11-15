# -*- coding: utf-8 -*-
"""
Фідбек на сигнали скринера (v2.2): оцінка Michel і «сховати зону».

- Оцінка: like («мій сетап») / dislike («не мій») / stale («неактуально») + причина й коментар.
  Кожна оцінка дописується в <папка результатів>/feedback_log.csv разом з УСІМА ознаками сигналу на той момент —
  це матеріал для майбутнього звіту «що показує фідбек» (шар 3). Остання оцінка по зоні — у feedback_last.json.
- Сховані зони — <папка результатів>/muted.json. Зона = символ|напрямок|нижня межа (як live_key у скринері),
  тож сховане діє, доки існує саме ця зона: нова зона на тій самій монеті показується знову.
  Записи про зони, яких немає в списку понад PRUNE_DAYS днів, прибираються самі.
- Логіку скринера це не змінює: сховані зони відсіює лише обгортка screener_runner.py перед алертами,
  а додаток — у списку й уточненні 15m.

Лише стандартна бібліотека; Python 3.9.
"""
import csv
import datetime as dt
import json
import os
import threading

MUTED_FILE = "muted.json"
LAST_FILE = "feedback_last.json"
LOG_FILE = "feedback_log.csv"
PRUNE_DAYS = 7

VERDICTS = {"like": "мій сетап", "dislike": "не мій", "stale": "неактуально"}
# причини для «не мій» / «неактуально» (ключ — у лог, назва — в інтерфейс)
REASONS = {
    "knife": "ніж / волатильно",
    "htf": "проти старшого тренду",
    "late": "зона задалеко або вже відпрацьована",
    "liq": "ліквідність поруч",
    "target": "мала ціль / погане R:R",
    "other": "інше",
}
# ознаки сигналу, що пишуться в лог разом з оцінкою (поля latest_app.json, без графіка)
FEATURES = ["status", "rank", "score", "price", "zone_low", "zone_high", "entry", "stop", "target", "rr", "rr_mid", "dist",
            "fib", "fib_ok", "fvg", "sweep", "vol_conf", "volume", "atr_pct", "knife", "struct", "against", "zone_type",
            "bos_time", "bos_bars_ago", "flags"]
LOG_HEAD = ["time", "market", "key", "symbol", "dir", "verdict", "reason", "note", "muted"] + FEATURES

_lock = threading.Lock()


def now_iso():
    return dt.datetime.now().isoformat(timespec="seconds")


def zone_key(symbol, direction, zone_low):
    """Той самий формат, що live_key у скринері й _key у screener_runner.py."""
    return f"{symbol}|{direction}|{float(zone_low):.8g}"


def row_key(r):
    return zone_key(r["symbol"], r["dir"], r["zone_low"])


def _read(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def _write(path, obj):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def muted(out_dir):
    """{ключ зони: {at, verdict, reason, last_seen}}"""
    m = _read(os.path.join(out_dir, MUTED_FILE), {})
    return m if isinstance(m, dict) else {}


def muted_keys(out_dir):
    return set(muted(out_dir))


def last_feedback(out_dir):
    m = _read(os.path.join(out_dir, LAST_FILE), {})
    return m if isinstance(m, dict) else {}


def drop_muted(out_dir, rows):
    """Рядки без схованих зон (для алертів). Не схованих — без змін, у тому ж порядку."""
    m = muted_keys(out_dir)
    if not m:
        return list(rows)
    return [r for r in rows if not (isinstance(r, dict) and r.get("zone_low") is not None and row_key(r) in m)]


def touch_seen(out_dir, rows, prune_days=PRUNE_DAYS, now=None):
    """Після скану: схованим зонам, що досі у списку, оновити last_seen; давно зниклі — прибрати. Повертає к-сть прибраних."""
    now = now or dt.datetime.now()
    with _lock:
        m = muted(out_dir)
        if not m:
            return 0
        present = {row_key(r) for r in rows if isinstance(r, dict) and r.get("zone_low") is not None}
        removed = 0
        for k in list(m):
            if k in present:
                m[k]["last_seen"] = now.isoformat(timespec="seconds")
                continue
            try:
                seen = dt.datetime.fromisoformat(m[k].get("last_seen") or m[k].get("at"))
            except (TypeError, ValueError):
                seen = now
            if (now - seen).days >= prune_days:
                del m[k]
                removed += 1
        _write(os.path.join(out_dir, MUTED_FILE), m)
        return removed


def record(out_dir, row, verdict, reason="", note="", mute=False, market="crypto"):
    """Записати оцінку сигналу (і за бажанням сховати зону). row — рядок з latest_app.json."""
    if verdict not in VERDICTS:
        raise ValueError("оцінка: like / dislike / stale")
    reason = reason or ""
    if reason and reason not in REASONS:
        raise ValueError("невідома причина")
    if verdict == "like" and mute:
        raise ValueError("«мій сетап» не ховається")
    note = " ".join(str(note or "").split())[:300]
    key = row_key(row)
    at = now_iso()
    with _lock:
        path = os.path.join(out_dir, LOG_FILE)
        new = not os.path.exists(path)
        os.makedirs(out_dir, exist_ok=True)
        with open(path, "a", newline="", encoding="utf-8-sig" if new else "utf-8") as f:
            w = csv.writer(f)
            if new:
                w.writerow(LOG_HEAD)
            feats = []
            for k in FEATURES:
                v = row.get(k)
                feats.append("; ".join(map(str, v)) if isinstance(v, list) else ("" if v is None else v))
            w.writerow([at, market, key, row["symbol"], row["dir"], verdict, reason, note, "так" if mute else "ні"] + feats)
        last = last_feedback(out_dir)
        last[key] = {"verdict": verdict, "reason": reason, "note": note, "at": at}
        _write(os.path.join(out_dir, LAST_FILE), last)
        if mute:
            m = muted(out_dir)
            m[key] = {"at": at, "verdict": verdict, "reason": reason, "symbol": row["symbol"], "dir": row["dir"],
                      "last_seen": at}
            _write(os.path.join(out_dir, MUTED_FILE), m)
    return key


def unmute(out_dir, key):
    with _lock:
        m = muted(out_dir)
        if key not in m:
            return False
        del m[key]
        _write(os.path.join(out_dir, MUTED_FILE), m)
        return True
