#!/usr/bin/env python3
"""
VPN Config Server (Option A): HTTPS-отдача "сырого" VLESS + админ-API синхронизации.
- POST /admin/keys/upload  (защита: X-Auth-Token)  — загрузка новых ключей trial/month/year
- POST /admin/assign       (защита: X-Auth-Token)  — фиксация выдачи конкретного ключа user_id
- GET  /sub/<token>        (публично)              — возвращает "сырой" VLESS по token = base64("{user_id}_{type}")

Использование:
- Хранит данные в DATA_DIR (по умолчанию ./data). Для Render — подключите Persistent Disk.
- AUTH_TOKEN задайте в переменной окружения (длинная случайная строка).

У кнопки в боте:
deeplink = "https://deeplink.website/?url=" + urlencode("https://<your-app>.onrender.com/sub/<token>")
"""

import os
import re
import json
import base64
import logging
from datetime import datetime, timezone
from typing import Dict, List
from flask import Flask, request, Response, jsonify, abort

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("vpn_config_server")

# ---------------------- Конфиг/путь хранения ----------------------
DATA_DIR = os.environ.get("DATA_DIR", os.path.abspath("./data"))
os.makedirs(DATA_DIR, exist_ok=True)

KEYS_FILE = os.path.join(DATA_DIR, "keys_store.json")
SUBS_FILE = os.path.join(DATA_DIR, "subscriptions.json")

AUTH_TOKEN = os.environ.get("AUTH_TOKEN", "")  # ОБЯЗАТЕЛЬНО задайте в окружении на проде

# Формат KEYS_FILE:
# {
#   "trial": ["vless://...", ...],
#   "month": ["vless://...", ...],
#   "year":  ["vless://...", ...],
#   "used":  ["vless://...", ...]
# }
#
# Формат SUBS_FILE:
# {
#   "7741189969": {
#     "type": "trial",
#     "key": "vless://...",
#     "end_date": "2025-12-31T23:59:59",
#     "active": true
#   }
# }

# ---------------------- Утилиты хранения ----------------------
def _load_json(path: str, default):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка чтения {path}: {e}")
    return default


def _save_json(path: str, data):
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка записи {path}: {e}")


def load_keys_store() -> Dict[str, List[str]]:
    data = _load_json(KEYS_FILE, {})
    return {
        "trial": data.get("trial", []),
        "month": data.get("month", []),
        "year": data.get("year", []),
        "used": data.get("used", []),
    }


def save_keys_store(store: Dict[str, List[str]]):
    _save_json(KEYS_FILE, store)


def load_subscriptions() -> Dict[str, Dict]:
    return _load_json(SUBS_FILE, {})


def save_subscriptions(subs: Dict[str, Dict]):
    _save_json(SUBS_FILE, subs)


# ---------------------- Нормализация VLESS ----------------------
def normalize_vless_for_v2raytun(vless_key: str) -> str:
    """
    - убираем authority
    - добавляем encryption=none (если нет)
    - чистим fragment от нестандартных символов
    """
    try:
        if not vless_key.startswith("vless://"):
            return vless_key

        m = re.match(r"vless://([^@]+)@([^:]+):(\d+)\?(.+?)(?:#(.*))?$", vless_key)
        if not m:
            return vless_key

        uuid = m.group(1)
        host = m.group(2)
        port = m.group(3)
        params_str = m.group(4)
        fragment = m.group(5) or ""

        # Парсим параметры
        from urllib.parse import parse_qs, unquote
        params = parse_qs(params_str)

        normalized = {}
        for k, values in params.items():
            if k == "authority":
                continue
            normalized[k] = values[0] if values else ""

        if "encryption" not in normalized:
            normalized["encryption"] = "none"

        # Сборка
        parts = []
        for k, v in normalized.items():
            parts.append(f"{k}={v}" if v else k)
        new_params = "&".join(parts)

        new_key = f"vless://{uuid}@{host}:{port}?{new_params}"

        if fragment:
            frag_decoded = unquote(fragment)
            frag_clean = re.sub(r"[^\w\-]", "", frag_decoded)
            if frag_clean:
                new_key += f"#{frag_clean}"

        return new_key
    except Exception as e:
        logger.warning(f"normalize_vless_for_v2raytun fallback: {e}")
        # минимальный fallback по authority
        key = re.sub(r"[&?]authority=[^&]*(?=&|$)", "", vless_key)
        key = re.sub(r"[?&]&+", "?", key)
        key = re.sub(r"&+", "&", key)
        key = re.sub(r"[?&]$", "", key)
        return key


def _require_auth():
    token = request.headers.get("X-Auth-Token", "")
    if not AUTH_TOKEN or token != AUTH_TOKEN:
        abort(401, description="Unauthorized")


# ---------------------- Админ-API: загрузка ключей ----------------------
@app.route("/admin/keys/upload", methods=["POST"])
def admin_keys_upload():
    """
    Защита: X-Auth-Token: <AUTH_TOKEN>
    Принимает:
    {
      "trial": ["vless://...", ...],
      "month": ["vless://...", ...],
      "year":  ["vless://...", ...]
    }
    Ключи нормализуются, дубликаты/использованные отбрасываются.
    """
    _require_auth()

    payload = request.get_json(silent=True) or {}
    incoming = {
        "trial": payload.get("trial", []) or [],
        "month": payload.get("month", []) or [],
        "year": payload.get("year", []) or [],
    }

    store = load_keys_store()
    used = set(store.get("used", []))
    changed = {"trial": 0, "month": 0, "year": 0}

    for t in ("trial", "month", "year"):
        bucket = set(store.get(t, []))
        for k in incoming[t]:
            if not k.startswith("vless://"):
                continue
            nk = normalize_vless_for_v2raytun(k)
            if nk not in bucket and nk not in used:
                bucket.add(nk)
                changed[t] += 1
        store[t] = sorted(bucket)

    save_keys_store(store)
    return jsonify({"success": True, "added": changed, "total": {t: len(store[t]) for t in ("trial", "month", "year")}})


# ---------------------- Админ-API: фиксация выдачи ----------------------
@app.route("/admin/assign", methods=["POST"])
def admin_assign():
    """
    Защита: X-Auth-Token: <AUTH_TOKEN>
    Принимает:
    {
      "user_id": 7741189969,
      "type": "trial"|"month"|"year",
      "key": "vless://...",
      "end_date": "2025-12-31T23:59:59"
    }
    Создает/обновляет подписку и помечает ключ как использованный.
    """
    _require_auth()
    data = request.get_json(silent=True) or {}

    user_id = data.get("user_id")
    sub_type = data.get("type")
    key = data.get("key") or ""
    end_date = data.get("end_date") or ""

    if not user_id or sub_type not in ("trial", "month", "year") or not key:
        return jsonify({"success": False, "error": "user_id/type/key required"}), 400

    # Нормализуем ключ
    key = normalize_vless_for_v2raytun(key)

    # Сохраняем подписку
    subs = load_subscriptions()
    subs[str(user_id)] = {
        "type": sub_type,
        "key": key,
        "end_date": end_date,
        "active": True,
        "updated_at": datetime.now(timezone.utc).isoformat()
    }
    save_subscriptions(subs)

    # Помечаем ключ как использованный, удаляем из available
    store = load_keys_store()
    for t in ("trial", "month", "year"):
        if key in store.get(t, []):
            store[t].remove(key)
    used = set(store.get("used", []))
    used.add(key)
    store["used"] = sorted(used)
    save_keys_store(store)

    return jsonify({"success": True})


# ---------------------- Публичный эндпоинт: выдача ключа по token ----------------------
@app.route("/sub/<token>", methods=["GET"])
def get_subscription(token: str):
    """
    token = base64("{user_id}_{type}")
    Возвращает "сырой" VLESS с заголовком text/plain; charset=utf-8
    """
    try:
        decoded = base64.b64decode(token).decode("utf-8")
        parts = decoded.split("_", 1)
        if len(parts) != 2:
            return Response("Bad token", status=400, mimetype="text/plain")
        user_id_str, sub_type = parts[0], parts[1]

        subs = load_subscriptions()
        sub = subs.get(user_id_str)
        if not sub or not sub.get("active"):
            return Response("Subscription inactive", status=403, mimetype="text/plain")

        # Проверка типа подписки
        if sub.get("type") != sub_type:
            return Response("Wrong subscription type", status=400, mimetype="text/plain")

        # Проверка срока (если end_date указан)
        end_iso = sub.get("end_date")
        if end_iso:
            try:
                end_dt = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
                if datetime.now(timezone.utc) > end_dt.replace(tzinfo=timezone.utc):
                    return Response("Subscription expired", status=403, mimetype="text/plain")
            except Exception:
                pass

        key = sub.get("key") or ""
        if not key.startswith("vless://"):
            return Response("Key not found", status=404, mimetype="text/plain")

        # Нормализуем на всякий случай
        key = normalize_vless_for_v2raytun(key)

        # Отдаем "сырой" VLESS — это важно для корректного импорта в V2RayTun
        return Response(
            key,
            status=200,
            mimetype="text/plain",
            headers={
                "Content-Type": "text/plain; charset=utf-8",
                "Access-Control-Allow-Origin": "*",
                "Cache-Control": "no-cache, no-store, must-revalidate",
                "Pragma": "no-cache",
                "Expires": "0",
                "Content-Disposition": 'inline; filename="config.sub"',
                # Доп. заголовки, распознаваемые некоторыми клиентами:
                "profile-update-interval": "24",
            }
        )
    except Exception as e:
        logger.error(f"/sub error: {e}")
        return Response("Internal server error", status=500, mimetype="text/plain")


@app.route("/health")
def health():
    return Response("OK", status=200, mimetype="text/plain")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "5000"))
    logger.info(f"DATA_DIR: {DATA_DIR}")
    logger.info(f"Keys file: {KEYS_FILE}")
    logger.info(f"Subs file: {SUBS_FILE}")
    app.run(host="0.0.0.0", port=port, debug=False)
