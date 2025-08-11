#!/usr/bin/env python3
from flask import Flask, request, Response, jsonify
import os, logging, uuid, base64, tempfile
from datetime import datetime

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

SUB_DIR = os.path.join(tempfile.gettempdir(), 'v2raytun_subs')
os.makedirs(SUB_DIR, exist_ok=True)

@app.get("/health")
def health():
    return jsonify(status="ok", ts=datetime.now().isoformat())

def make_sub_content(vless: str, profile_name: str) -> str:
    if "#" not in vless:
        vless = f"{vless}#{profile_name}"
    return base64.b64encode(vless.encode("utf-8")).decode("utf-8")

@app.post("/upload")
def upload():
    data = request.get_json(silent=True) or {}
    vless = data.get("config", "")
    profile = data.get("profile_name", "VPN")
    if not vless.startswith("vless://"):
        return jsonify(success=False, error="Only vless:// supported"), 400
    sub_id = str(uuid.uuid4())[:12]
    path = os.path.join(SUB_DIR, f"{sub_id}.sub")
    with open(path, "w", encoding="utf-8") as f:
        f.write(make_sub_content(vless, profile))
    base = request.host_url.rstrip("/")
    return jsonify(success=True,
                   sub_id=sub_id,
                   sub_url=f"{base}/sub/{sub_id}",
                   raw_url=f"{base}/sub/{sub_id}/raw")

@app.get("/sub/<sub_id>")
def get_sub(sub_id):
    path = os.path.join(SUB_DIR, f"{sub_id}.sub")
    if not os.path.exists(path): return "Not found", 404
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
    return Response(content, mimetype="text/plain",
                    headers={"Content-Type": "text/plain; charset=utf-8"})

@app.get("/sub/<sub_id>/raw")
def get_sub_raw(sub_id):
    path = os.path.join(SUB_DIR, f"{sub_id}.sub")
    if not os.path.exists(path): return "Not found", 404
    with open(path, "r", encoding="utf-8") as f:
        b64 = f.read()
    try:
        raw = base64.b64decode(b64).decode("utf-8")
    except Exception:
        raw = b64
    return Response(raw, mimetype="text/plain",
                    headers={"Content-Type": "text/plain; charset=utf-8"})

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5004))
    app.run(host="0.0.0.0", port=port)
