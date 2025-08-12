#!/usr/bin/env python3
"""
API сервер для создания .sub файлов совместимых с V2RayTun
Принимает vless:// ключи и отдает готовые HTTPS ссылки на .sub файлы
"""

from flask import Flask, request, Response, jsonify
import os
import logging
import uuid
import base64
import tempfile
from datetime import datetime
from urllib.parse import quote

app = Flask(__name__)
logging.basicConfig(level=logging.INFO)

# Создаем папку для .sub файлов
SUB_FILES_DIR = os.path.join(tempfile.gettempdir(), 'v2raytun_subs')
if not os.path.exists(SUB_FILES_DIR):
    os.makedirs(SUB_FILES_DIR)

logger = logging.getLogger(__name__)

@app.route('/health')
def health():
    """Проверка работоспособности API"""
    return jsonify({
        "status": "ok", 
        "message": "V2RayTun Compatible Server is running",
        "timestamp": datetime.now().isoformat()
    })

@app.route('/upload', methods=['POST'])
def upload_config():
    """
    Принимает VLESS ключ и создает .sub файл для V2RayTun
    
    POST /upload
    Content-Type: application/json
    {
        "config": "vless://...",
        "profile_name": "VPN"
    }
    """
    try:
        data = request.get_json()
        
        if not data:
            return jsonify({"success": False, "error": "JSON data required"}), 400
        
        vless_config = data.get('config')
        profile_name = data.get('profile_name', 'VPN')
        
        if not vless_config:
            return jsonify({"success": False, "error": "config field is required"}), 400
        
        if not vless_config.startswith('vless://'):
            return jsonify({"success": False, "error": "Only vless:// configs are supported"}), 400
        
        # Генерируем уникальный ID для .sub файла
        sub_id = str(uuid.uuid4())[:12]
        
        # Создаем .sub файл в base64 формате (стандарт для subscription)
        sub_content = create_subscription_content(vless_config, profile_name)
        
        # Сохраняем .sub файл
        sub_filename = f"{sub_id}.sub"
        sub_filepath = os.path.join(SUB_FILES_DIR, sub_filename)
        
        with open(sub_filepath, 'w', encoding='utf-8') as f:
            f.write(sub_content)
        
        # Создаем публично-доступную ссылку на .sub файл, опираясь на реальный хост из запроса
        # Пример: http://192.168.1.133:5005/sub/<id>
        host_url = request.host_url.rstrip('/')
        sub_url = f"{host_url}/sub/{sub_id}"
        
        logger.info(f"✅ Создан .sub файл {sub_id} для профиля '{profile_name}'")
        
        return jsonify({
            "success": True,
            "sub_id": sub_id,
            "sub_url": sub_url,
            "profile_name": profile_name,
            "file_path": sub_filepath,
            "created_at": datetime.now().isoformat()
        })
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания .sub файла: {e}")
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/sub/<sub_id>')
def get_subscription(sub_id):
    """
    Отдает .sub файл по HTTPS
    
    GET /sub/{sub_id}
    Returns: base64 encoded subscription content
    """
    try:
        sub_filepath = os.path.join(SUB_FILES_DIR, f"{sub_id}.sub")
        
        if not os.path.exists(sub_filepath):
            logger.warning(f"⚠️ .sub файл не найден: {sub_id}")
            return "Subscription not found", 404
        
        with open(sub_filepath, 'r', encoding='utf-8') as f:
            sub_content = f.read()
        
        # Возвращаем .sub файл с правильными заголовками
        response = Response(
            sub_content,
            mimetype='text/plain',
            headers={
                'Content-Type': 'text/plain; charset=utf-8',
                'Access-Control-Allow-Origin': '*',
                'Cache-Control': 'no-cache',
                'Content-Disposition': f'attachment; filename="{sub_id}.sub"',
                'subscription-userinfo': f'upload=0; download=0; total=0; expire=0'
            }
        )
        
        logger.info(f"📤 Отдан .sub файл {sub_id}")
        return response
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения .sub файла {sub_id}: {e}")
        return f"Error: {str(e)}", 500

@app.route('/sub/<sub_id>/raw')
def get_subscription_raw(sub_id):
    """
    Отдает расшифрованный .sub файл для отладки
    
    GET /sub/{sub_id}/raw
    Returns: decoded subscription content
    """
    try:
        sub_filepath = os.path.join(SUB_FILES_DIR, f"{sub_id}.sub")
        
        if not os.path.exists(sub_filepath):
            return "Subscription not found", 404
        
        with open(sub_filepath, 'r', encoding='utf-8') as f:
            encoded_content = f.read()
        
        # Декодируем base64
        try:
            decoded_content = base64.b64decode(encoded_content).decode('utf-8')
        except:
            decoded_content = encoded_content  # Если не base64, отдаем как есть
        
        return Response(
            decoded_content,
            mimetype='text/plain',
            headers={'Content-Type': 'text/plain; charset=utf-8'}
        )
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения raw .sub файла {sub_id}: {e}")
        return f"Error: {str(e)}", 500


@app.route('/open/<sub_id>')
def open_page(sub_id: str):
    """Простая HTML‑страница с кнопками запуска V2RayTun.
    1) import-config?url=<HTTPS sub>
    2) add?config=<RAW VLESS из /raw>
    """
    try:
        sub_url = f"{request.host_url.rstrip('/')}/sub/{sub_id}"
        raw_url = f"{sub_url}/raw"

        # v2raytun deep links
        v2raytun_import = f"v2raytun://import-config?url={quote(sub_url, safe='')}"

        # Получаем raw vless, чтобы предложить второй способ (add?config=)
        raw_resp = app.test_client().get(f"/sub/{sub_id}/raw")
        vless_raw = raw_resp.get_data(as_text=True) if raw_resp.status_code == 200 else ''
        v2raytun_add = (
            f"v2raytun://add?config={quote(vless_raw, safe='')}" if vless_raw.startswith('vless://') else ''
        )

        html = f"""
<!doctype html>
<html lang='ru'>
<meta charset='utf-8'/>
<meta name='viewport' content='width=device-width, initial-scale=1'/>
<title>Подключение VPN</title>
<body style="font-family:system-ui,-apple-system,Segoe UI,Roboto,Arial;max-width:600px;margin:40px auto;padding:0 16px;">
  <h2>Подключение VPN</h2>
  <p>Нажмите кнопку ниже, чтобы импортировать конфигурацию в V2RayTun.</p>
  <p><a href="{v2raytun_import}" style="display:inline-block;padding:12px 18px;background:#1e88e5;color:#fff;text-decoration:none;border-radius:8px;">Открыть в V2RayTun</a></p>
  <details style="margin-top:12px;">
    <summary>Альтернативные способы</summary>
    <div style="margin-top:8px;">
      {'<p><a href="'+v2raytun_add+'">Открыть через add?config=</a></p>' if v2raytun_add else ''}
      <p><a href="https://deeplink.website/?url={quote(v2raytun_import, safe='')}">Через deeplink.website</a></p>
      <p><a href="{sub_url}">Скачать .sub</a> · <a href="{raw_url}">Показать vless</a></p>
    </div>
  </details>
</body>
</html>
"""
        return Response(html, mimetype='text/html; charset=utf-8')
    except Exception as e:
        logger.error(f"Ошибка open_page: {e}")
        return "Internal error", 500

@app.route('/list')
def list_subscriptions():
    """Список всех .sub файлов (для отладки)"""
    try:
        subs = []
        for filename in os.listdir(SUB_FILES_DIR):
            if filename.endswith('.sub'):
                sub_id = filename[:-4]  # убираем .sub
                sub_filepath = os.path.join(SUB_FILES_DIR, filename)
                
                # Получаем информацию о файле
                stat = os.stat(sub_filepath)
                created_time = datetime.fromtimestamp(stat.st_ctime).isoformat()
                
                subs.append({
                    "sub_id": sub_id,
                    "url": f"http://localhost:5004/sub/{sub_id}",
                    "raw_url": f"http://localhost:5004/sub/{sub_id}/raw",
                    "created_at": created_time,
                    "size_bytes": stat.st_size
                })
        
        return jsonify({
            "subscriptions": subs, 
            "total": len(subs),
            "directory": SUB_FILES_DIR
        })
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения списка .sub файлов: {e}")
        return jsonify({"error": str(e)}), 500

def create_subscription_content(vless_config: str, profile_name: str) -> str:
    """
    Создает содержимое .sub файла в правильном формате для V2RayTun
    
    Args:
        vless_config (str): VLESS конфигурация
        profile_name (str): Имя профиля
        
    Returns:
        str: base64 закодированное содержимое .sub файла
    """
    try:
        # V2RayTun ожидает base64 закодированный список конфигураций
        # Каждая конфигурация на отдельной строке
        
        # Добавляем имя профиля в конец VLESS ключа если его там нет
        if '#' not in vless_config:
            vless_with_name = f"{vless_config}#{profile_name}"
        else:
            vless_with_name = vless_config
        
        # Кодируем в base64 (стандарт для subscription файлов)
        content_bytes = vless_with_name.encode('utf-8')
        base64_content = base64.b64encode(content_bytes).decode('utf-8')
        
        logger.info(f"📝 Создано содержимое .sub для профиля '{profile_name}'")
        return base64_content
        
    except Exception as e:
        logger.error(f"❌ Ошибка создания содержимого .sub: {e}")
        # Fallback - возвращаем оригинальный ключ
        return base64.b64encode(vless_config.encode('utf-8')).decode('utf-8')

# Дополнительные утилиты для отладки
@app.route('/test')
def test_endpoint():
    """Тестовый endpoint для проверки работы API"""
    test_vless = "vless://12345678-90ab-cdef-1234-567890abcdef@vpn.example.com:443?security=tls&sni=vpn.example.com&type=ws&host=vpn.example.com&path=%2F#TestVPN"
    
    # Симулируем создание .sub файла
    try:
        import requests
        response = requests.post(
            "http://localhost:5004/upload",
            json={"config": test_vless, "profile_name": "Test Profile"}
        )
        return jsonify({
            "test_request": True,
            "api_response": response.json() if response.status_code == 200 else response.text,
            "status_code": response.status_code
        })
    except Exception as e:
        return jsonify({"test_request": True, "error": str(e)})

if __name__ == '__main__':
    import os
    port = int(os.environ.get('PORT', 5004))
    print("🚀 Запуск V2RayTun Compatible Server...")
    print("📂 .sub файлы сохраняются в:", SUB_FILES_DIR)
    print(f"🔧 Здоровье сервера: http://localhost:{port}/health")
    print(f"📋 Список .sub файлов: http://localhost:{port}/list")
    print(f"🧪 Тестовый endpoint: http://localhost:{port}/test")
    print(f"📡 API endpoint: POST http://localhost:{port}/upload")
    print("--" * 30)
    
    app.run(host='0.0.0.0', port=port, debug=False)
