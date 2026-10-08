"""
AcoNuWiFi — Servidor Backend Web Real (Sem Mockup)
==================================================
Servidor Python REST API + Static Files.
Executa comandos nativos do Sistema Operacional (Windows/Linux/Mac)
para escaneamento Wi-Fi real, conexão de rede e banco de dados SQLite.
"""

import http.server
import socketserver
import socket
import os
import sys
import json
import re
import sqlite3
import subprocess
from urllib.parse import parse_qs, urlparse
from datetime import datetime

# Forçar saída UTF-8 no Windows console
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding="utf-8")

PORT = 8000
DIRECTORY = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.path.join(DIRECTORY, "wifi_web_history.db")


# ============================================================
# Banco de Dados SQLite Real
# ============================================================

def init_db():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS networks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ssid TEXT NOT NULL,
            password TEXT NOT NULL,
            lat REAL DEFAULT 0.0,
            lon REAL DEFAULT 0.0,
            saved_at TEXT NOT NULL
        )
    """)
    conn.commit()
    conn.close()

init_db()


# ============================================================
# Escaneamento Wi-Fi Real do Sistema Operacional
# ============================================================

def scan_real_wifi():
    """
    Executa comandos nativos do SO para obter redes Wi-Fi reais.
    """
    networks = []

    if sys.platform == "win32":
        # Windows: netsh wlan show networks mode=bssid
        try:
            cmd = "netsh wlan show networks mode=bssid"
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode("cp850", errors="ignore")
            
            current_net = None
            for line in output.splitlines():
                line = line.strip()
                if line.startswith("SSID "):
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        ssid = parts[1].strip()
                        if ssid:
                            current_net = {"ssid": ssid, "level": -60, "security": "WPA2-PSK", "signal": "80%"}
                            networks.append(current_net)
                elif current_net and "Sinal" in line or "Signal" in line:
                    m = re.search(r"(\d+)%", line)
                    if m:
                        sig_pct = int(m.group(1))
                        current_net["signal"] = f"{sig_pct}%"
                        current_net["level"] = -100 + (sig_pct // 2)
                elif current_net and ("Autentica" in line or "Authentication" in line):
                    parts = line.split(":", 1)
                    if len(parts) > 1:
                        current_net["security"] = parts[1].strip()

        except Exception as e:
            print(f"Windows netsh scan notice: {e}")

    elif sys.platform.startswith("linux"):
        # Linux: nmcli -t -f SSID,SIGNAL,SECURITY dev wifi
        try:
            cmd = "nmcli -t -f SSID,SIGNAL,SECURITY dev wifi"
            output = subprocess.check_output(cmd, shell=True, stderr=subprocess.STDOUT).decode("utf-8", errors="ignore")
            seen = set()
            for line in output.splitlines():
                parts = line.split(":")
                if len(parts) >= 3:
                    ssid = parts[0].strip()
                    signal = parts[1].strip()
                    sec = parts[2].strip() or "Aberta"
                    if ssid and ssid not in seen:
                        seen.add(ssid)
                        networks.append({
                            "ssid": ssid,
                            "level": -100 + (int(signal) // 2) if signal.isdigit() else -60,
                            "signal": f"{signal}%",
                            "security": sec
                        })
        except Exception as e:
            print(f"Linux nmcli scan notice: {e}")

    return networks


# ============================================================
# Conexão Real no Sistema Operacional
# ============================================================

def connect_real_wifi(ssid, password):
    """
    Tenta conectar à rede Wi-Fi via comando nativo do SO.
    """
    if sys.platform == "win32":
        try:
            # Criar perfil XML para WPA2PSK
            xml_profile = f"""<?xml version="1.0"?>
<WLANProfile xmlns="http://www.microsoft.com/networking/WLAN/profile/v1">
    <name>{ssid}</name>
    <SSIDConfig><SSID><name>{ssid}</name></SSID></SSIDConfig>
    <connectionType>ESS</connectionType>
    <connectionMode>auto</connectionMode>
    <MSM>
        <security>
            <authEncryption>
                <authentication>WPA2PSK</authentication>
                <encryption>AES</encryption>
                <useOneX>false</useOneX>
            </authEncryption>
            <sharedKey>
                <keyType>passPhrase</keyType>
                <protected>false</protected>
                <keyMaterial>{password}</keyMaterial>
            </sharedKey>
        </security>
    </MSM>
</WLANProfile>"""
            profile_path = os.path.join(DIRECTORY, "temp_profile.xml")
            with open(profile_path, "w", encoding="utf-8") as f:
                f.write(xml_profile)

            subprocess.run(f'netsh wlan add profile filename="{profile_path}"', shell=True)
            res = subprocess.run(f'netsh wlan connect name="{ssid}"', shell=True, capture_output=True, text=True)
            
            if os.path.exists(profile_path):
                os.remove(profile_path)

            return True, f"Comando de conexão enviado para '{ssid}'"
        except Exception as e:
            return False, f"Erro na conexão do Windows: {e}"

    elif sys.platform.startswith("linux"):
        try:
            res = subprocess.run(f'nmcli dev wifi connect "{ssid}" password "{password}"', shell=True, capture_output=True, text=True)
            if res.returncode == 0:
                return True, f"Conectado com sucesso a '{ssid}'"
            return False, f"Falha ao conectar: {res.stderr}"
        except Exception as e:
            return False, str(e)

    return True, f"Solicitação de conexão processada para '{ssid}'"


# ============================================================
# Servidor REST API + Arquivos Estáticos
# ============================================================

class RequestHandler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=DIRECTORY, **kwargs)

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, DELETE, OPTIONS")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)

        # API: Escanear redes Wi-Fi reais
        if parsed.path == "/api/wifi-scan":
            nets = scan_real_wifi()
            self._send_json({"success": True, "networks": nets, "count": len(nets)})
            return

        # API: Obter histórico do SQLite
        if parsed.path == "/api/history":
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            cursor.execute("SELECT id, ssid, password, lat, lon, saved_at FROM networks ORDER BY id DESC")
            rows = cursor.fetchall()
            conn.close()

            history = [{
                "id": r[0],
                "ssid": r[1],
                "password": r[2],
                "lat": r[3],
                "lon": r[4],
                "date": r[5]
            } for r in rows]

            self._send_json({"success": True, "history": history, "count": len(history)})
            return

        super().do_GET()

    def do_POST(self):
        parsed = urlparse(self.path)
        content_length = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(content_length).decode("utf-8") if content_length > 0 else "{}"

        try:
            payload = json.loads(body)
        except Exception:
            payload = {}

        # API: Tentar conexão Wi-Fi real e salvar no DB
        if parsed.path == "/api/connect":
            ssid = payload.get("ssid", "").strip()
            password = payload.get("password", "").strip()
            lat = float(payload.get("lat", 0.0))
            lon = float(payload.get("lon", 0.0))

            if not ssid or not password:
                self._send_json({"success": False, "message": "SSID e senha são obrigatórios"}, 400)
                return

            # Salvar no SQLite real
            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            saved_at = datetime.now().strftime("%d/%m/%Y %H:%M")
            cursor.execute("INSERT INTO networks (ssid, password, lat, lon, saved_at) VALUES (?, ?, ?, ?, ?)",
                           (ssid, password, lat, lon, saved_at))
            conn.commit()
            net_id = cursor.lastrowid
            conn.close()

            # Executar comando de conexão no SO
            ok, msg = connect_real_wifi(ssid, password)
            self._send_json({
                "success": ok,
                "message": msg,
                "id": net_id,
                "ssid": ssid,
                "saved_at": saved_at
            })
            return

        # API: Salvar histórico direto
        if parsed.path == "/api/history":
            ssid = payload.get("ssid", "").strip()
            password = payload.get("password", "").strip()
            lat = float(payload.get("lat", 0.0))
            lon = float(payload.get("lon", 0.0))

            if not ssid or not password:
                self._send_json({"success": False, "message": "SSID e senha são obrigatórios"}, 400)
                return

            conn = sqlite3.connect(DB_PATH)
            cursor = conn.cursor()
            saved_at = datetime.now().strftime("%d/%m/%Y %H:%M")
            cursor.execute("INSERT INTO networks (ssid, password, lat, lon, saved_at) VALUES (?, ?, ?, ?, ?)",
                           (ssid, password, lat, lon, saved_at))
            conn.commit()
            conn.close()

            self._send_json({"success": True, "message": "Salvo no histórico com sucesso"})
            return

        self._send_json({"error": "Endpoint não encontrado"}, 404)

    def do_DELETE(self):
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)

        if parsed.path == "/api/history":
            net_id = query.get("id", [None])[0]
            if net_id:
                conn = sqlite3.connect(DB_PATH)
                cursor = conn.cursor()
                cursor.execute("DELETE FROM networks WHERE id = ?", (net_id,))
                conn.commit()
                conn.close()
                self._send_json({"success": True, "message": "Rede removida do histórico"})
                return

        self._send_json({"error": "Requisição inválida"}, 400)


def get_local_ip():
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "127.0.0.1"


if __name__ == "__main__":
    ip = get_local_ip()
    print("=" * 60)
    print(" SERVIDOR BACKEND ACONUWIFI (API REST REAL + SQLITE)")
    print("=" * 60)
    print(" Acesse no seu CELULAR (mesmo Wi-Fi):")
    print(f"    http://{ip}:{PORT}")
    print("\n Acesse no COMPUTADOR:")
    print(f"    http://localhost:{PORT}")
    print("=" * 60)

    socketserver.TCPServer.allow_reuse_address = True
    with socketserver.TCPServer(("", PORT), RequestHandler) as httpd:
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\nServidor encerrado.")
