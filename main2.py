"""
AcoNuWiFi v2 — Leitor de Senhas Wi-Fi por OCR
===============================================
Otimizado para compilação rápida com Buildozer.
OCR via Google ML Kit (nativo Android, sem torch/opencv).
Interface: KivyMD 1.2.0 (estável com Buildozer).
"""

import os
import re
import sqlite3
import threading
from datetime import datetime

os.environ["KIVY_LOG_LEVEL"] = "info"
from kivy.config import Config
Config.set("graphics", "width", "400")
Config.set("graphics", "height", "700")

from kivy.lang import Builder
from kivy.clock import Clock
from kivy.utils import platform
from kivy.properties import StringProperty, NumericProperty
from kivy.metrics import dp
from kivy.uix.behaviors import ButtonBehavior

from kivymd.app import MDApp
from kivymd.uix.screen import MDScreen
from kivymd.uix.card import MDCard
from kivymd.uix.label import MDLabel
from kivymd.uix.snackbar import Snackbar


# ============================================================
# Regex para detectar senhas em texto OCR
# ============================================================

_PATTERNS = [re.compile(p, re.IGNORECASE) for p in [
    r"senha\s*(?:do\s*)?wi-?fi\s*[:=]?\s*[\"']?([^\s\"'\n]+)",
    r"password\s*[:=]?\s*[\"']?([^\s\"'\n]+)",
    r"senha\s*[:=]?\s*[\"']?([^\s\"'\n]+)",
    r"pass\s*[:=]?\s*[\"']?([^\s\"'\n]+)",
    r"pwr\s*[:=]?\s*[\"']?([^\s\"'\n]+)",
    r"pwd\s*[:=]?\s*[\"']?([^\s\"'\n]+)",
    r"key\s*[:=]?\s*[\"']?([^\s\"'\n]+)",
    r"chave\s*[:=]?\s*[\"']?([^\s\"'\n]+)",
    r"wi-?fi\s*[:=]?\s*[\"']?([^\s\"'\n]+)",
    r"c[oó]digo\s*[:=]?\s*[\"']?([^\s\"'\n]+)",
]]


def extract_password(text):
    """Extrai senha Wi-Fi de texto reconhecido por OCR."""
    if not text:
        return ""
    text = text.strip()

    # Busca por palavras-chave (senha:, password:, etc.)
    for rx in _PATTERNS:
        m = rx.search(text)
        if m:
            pwd = m.group(1).strip(".,;:!?\"'")
            if len(pwd) >= 4:
                return pwd

    # Fallback: sequência alfanumérica >= 8 chars
    tokens = re.findall(r"[A-Za-z0-9@#$%!&*]{8,}", text)
    if tokens:
        return max(tokens, key=len)

    # Último fallback: última linha não vazia
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    return lines[-1] if lines else text


# ============================================================
# Banco de Dados SQLite
# ============================================================

class WiFiDatabase:
    """Armazenamento local de redes Wi-Fi salvas."""

    def __init__(self):
        if platform == "android":
            from android.storage import app_storage_path
            path = os.path.join(app_storage_path(), "wifi_history.db")
        else:
            path = "wifi_history.db"

        self.conn = sqlite3.connect(path, check_same_thread=False)
        self.lock = threading.Lock()
        with self.lock:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS networks (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ssid TEXT NOT NULL,
                    password TEXT NOT NULL,
                    lat REAL DEFAULT 0,
                    lon REAL DEFAULT 0,
                    saved_at TEXT NOT NULL
                )
            """)
            self.conn.commit()

    def save(self, ssid, password, lat=0.0, lon=0.0):
        with self.lock:
            self.conn.execute(
                "INSERT INTO networks (ssid,password,lat,lon,saved_at) VALUES (?,?,?,?,?)",
                (ssid, password, lat, lon, datetime.now().isoformat()),
            )
            self.conn.commit()

    def get_all(self):
        with self.lock:
            return self.conn.execute(
                "SELECT id,ssid,password,lat,lon,saved_at FROM networks ORDER BY id DESC"
            ).fetchall()

    def count(self):
        with self.lock:
            return self.conn.execute("SELECT COUNT(*) FROM networks").fetchone()[0]

    def delete(self, net_id):
        with self.lock:
            self.conn.execute("DELETE FROM networks WHERE id=?", (net_id,))
            self.conn.commit()

    def close(self):
        self.conn.close()


# ============================================================
# Serviço Android — Wi-Fi, GPS, Permissões (Pyjnius)
# ============================================================

class AndroidService:
    """Encapsula chamadas nativas Android. No desktop, fornece stubs."""

    def __init__(self):
        self.is_android = platform == "android"

    def request_permissions(self):
        if not self.is_android:
            return
        from android.permissions import request_permissions, Permission
        request_permissions([
            Permission.CAMERA,
            Permission.ACCESS_FINE_LOCATION,
            Permission.ACCESS_COARSE_LOCATION,
            Permission.ACCESS_WIFI_STATE,
            Permission.CHANGE_WIFI_STATE,
            Permission.ACCESS_NETWORK_STATE,
            Permission.CHANGE_NETWORK_STATE,
        ])

    def scan_wifi(self):
        """Lista redes Wi-Fi ao redor. Retorna [{'ssid','level','capabilities'}]."""
        if not self.is_android:
            return [
                {"ssid": "MinhaRede_5G", "level": -42, "capabilities": "[WPA2-PSK]"},
                {"ssid": "Vizinho_Net", "level": -58, "capabilities": "[WPA2-PSK]"},
                {"ssid": "CafeWiFi", "level": -67, "capabilities": "[WPA-PSK]"},
                {"ssid": "Escritorio_4A", "level": -53, "capabilities": "[WPA2-PSK]"},
                {"ssid": "RedeAberta", "level": -75, "capabilities": "[ESS]"},
            ]

        try:
            from jnius import autoclass
            Context = autoclass("android.content.Context")
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            wm = activity.getSystemService(Context.WIFI_SERVICE)
            wm.startScan()
            results = wm.getScanResults()
            nets, seen = [], set()
            for i in range(results.size()):
                r = results.get(i)
                if r.SSID and r.SSID not in seen:
                    seen.add(r.SSID)
                    nets.append({
                        "ssid": r.SSID,
                        "level": r.level,
                        "capabilities": r.capabilities,
                    })
            nets.sort(key=lambda x: x["level"], reverse=True)
            return nets
        except Exception as e:
            print(f"WiFi scan error: {e}")
            return []

    def connect(self, ssid, password):
        """Conecta à rede Wi-Fi. Retorna (sucesso, mensagem)."""
        if not self.is_android:
            return True, "Conexão simulada (desktop)"

        try:
            from jnius import autoclass
            Build = autoclass("android.os.Build")
            if Build.VERSION.SDK_INT >= 29:
                return self._connect_api29(ssid, password)
            else:
                return self._connect_legacy(ssid, password)
        except Exception as e:
            return False, str(e)

    def _connect_legacy(self, ssid, password):
        """Android < 10 — WifiConfiguration."""
        from jnius import autoclass
        Context = autoclass("android.content.Context")
        WifiConfiguration = autoclass("android.net.wifi.WifiConfiguration")
        activity = autoclass("org.kivy.android.PythonActivity").mActivity
        wm = activity.getSystemService(Context.WIFI_SERVICE)

        if not wm.isWifiEnabled():
            wm.setWifiEnabled(True)

        config = WifiConfiguration()
        config.SSID = f'"{ssid}"'
        config.preSharedKey = f'"{password}"'
        net_id = wm.addNetwork(config)
        if net_id == -1:
            return False, "Falha ao configurar rede"

        wm.disconnect()
        ok = wm.enableNetwork(net_id, True)
        wm.reconnect()
        return ok, "Conectando..." if ok else "Falha ao habilitar rede"

    def _connect_api29(self, ssid, password):
        """Android >= 10 — WifiNetworkSuggestion."""
        from jnius import autoclass
        Context = autoclass("android.content.Context")
        Builder = autoclass("android.net.wifi.WifiNetworkSuggestion$Builder")
        ArrayList = autoclass("java.util.ArrayList")
        WifiManager = autoclass("android.net.wifi.WifiManager")
        activity = autoclass("org.kivy.android.PythonActivity").mActivity

        suggestion = (
            Builder()
            .setSsid(ssid)
            .setWpa2Passphrase(password)
            .setIsAppInteractionRequired(True)
            .build()
        )
        lst = ArrayList()
        lst.add(suggestion)

        wm = activity.getSystemService(Context.WIFI_SERVICE)
        status = wm.addNetworkSuggestions(lst)
        if status == WifiManager.STATUS_NETWORK_SUGGESTIONS_SUCCESS:
            return True, "Sugestão de rede enviada ao sistema"
        return False, f"Falha (código {status})"

    def get_location(self):
        """Retorna (lat, lon) ou (0, 0) se indisponível."""
        if not self.is_android:
            return (-23.5505, -46.6333)  # São Paulo stub

        try:
            from jnius import autoclass
            Context = autoclass("android.content.Context")
            LocationManager = autoclass("android.location.LocationManager")
            activity = autoclass("org.kivy.android.PythonActivity").mActivity
            lm = activity.getSystemService(Context.LOCATION_SERVICE)

            for provider in [LocationManager.GPS_PROVIDER, LocationManager.NETWORK_PROVIDER]:
                try:
                    loc = lm.getLastKnownLocation(provider)
                    if loc:
                        return (loc.getLatitude(), loc.getLongitude())
                except Exception:
                    continue
            return (0.0, 0.0)
        except Exception:
            return (0.0, 0.0)


# ============================================================
# OCR via Google ML Kit (Android) — Rápido, Leve, Offline
# ============================================================

class OCRService:
    """
    Android: Google ML Kit Text Recognition via pyjnius (gradle dep).
    Desktop: retorna texto stub para testes da interface.

    ML Kit é ~20MB no APK, mas compila em segundos (vs horas do PyTorch).
    Reconhece texto latino (pt/en/es) sem downloads extras.
    """

    def __init__(self):
        self.is_android = platform == "android"
        self._ready = False

    def initialize(self):
        """Pré-carrega as classes Java do ML Kit. Rodar em thread."""
        if not self.is_android:
            self._ready = True
            return

        try:
            from jnius import autoclass
            self._TextRecognition = autoclass(
                "com.google.mlkit.vision.text.TextRecognition"
            )
            self._Options = autoclass(
                "com.google.mlkit.vision.text.latin.TextRecognizerOptions"
            )
            self._InputImage = autoclass(
                "com.google.mlkit.vision.common.InputImage"
            )
            self._BitmapFactory = autoclass("android.graphics.BitmapFactory")
            self._Tasks = autoclass("com.google.android.gms.tasks.Tasks")
            self._TimeUnit = autoclass("java.util.concurrent.TimeUnit")
            self._ready = True
            print("ML Kit OCR pronto")
        except Exception as e:
            print(f"ML Kit init falhou: {e}")
            self._ready = False

    def read_image(self, image_path):
        """
        Lê texto de uma imagem. DEVE ser chamado de thread background
        (Tasks.await é bloqueante).
        """
        if not self.is_android:
            # Stub para testes no desktop
            return "Rede WiFi\nSenha: TestWiFi2024\nBem-vindo!"

        if not self._ready:
            return ""

        try:
            # Criar reconhecedor
            options = self._Options.Builder().build()
            recognizer = self._TextRecognition.getClient(options)

            # Carregar bitmap da imagem capturada
            bitmap = self._BitmapFactory.decodeFile(image_path)
            if bitmap is None:
                return ""

            # Criar InputImage a partir do bitmap (rotação 0 = sem rotação)
            input_image = self._InputImage.fromBitmap(bitmap, 0)

            # Processar OCR de forma síncrona (bloqueante, timeout 30s)
            task = recognizer.process(input_image)
            result = getattr(self._Tasks, 'await')(task, 30, self._TimeUnit.SECONDS)

            # Extrair texto reconhecido
            text = result.getText()

            # Liberar recursos
            recognizer.close()
            bitmap.recycle()

            return text if text else ""
        except Exception as e:
            print(f"ML Kit OCR error: {e}")
            return ""


# ============================================================
# Layout KV — KivyMD 1.2.0 (Compatível com Buildozer)
# ============================================================

KV = """
#:import dp kivy.metrics.dp

# ── Card de rede Wi-Fi ──
<WiFiCard>:
    orientation: "vertical"
    size_hint_y: None
    height: dp(72)
    padding: [dp(18), dp(14)]
    md_bg_color: 0.13, 0.13, 0.18, 1
    radius: [dp(12)]
    ripple_behavior: True
    on_release: app.on_network_selected(self.ssid)

    MDLabel:
        text: root.ssid
        font_style: "Subtitle1"
        theme_text_color: "Custom"
        text_color: 1, 1, 1, 0.95
        size_hint_y: None
        height: self.texture_size[1]

    MDLabel:
        text: root.info_text
        font_style: "Caption"
        theme_text_color: "Custom"
        text_color: 1, 1, 1, 0.4
        size_hint_y: None
        height: self.texture_size[1]


# ── Card de rede salva (histórico) ──
<HistoryCard>:
    orientation: "vertical"
    size_hint_y: None
    height: dp(90)
    padding: [dp(18), dp(12)]
    spacing: dp(2)
    md_bg_color: 0.13, 0.13, 0.18, 1
    radius: [dp(12)]
    ripple_behavior: True
    on_release: app.on_history_tap(self.network_id, self.ssid, self.password)

    MDLabel:
        text: root.ssid
        font_style: "Subtitle1"
        theme_text_color: "Custom"
        text_color: 1, 1, 1, 0.95
        size_hint_y: None
        height: self.texture_size[1]

    MDLabel:
        text: "Senha: " + root.password
        font_style: "Body2"
        theme_text_color: "Custom"
        text_color: 0.4, 0.85, 0.4, 1
        size_hint_y: None
        height: self.texture_size[1]

    MDLabel:
        text: root.saved_info
        font_style: "Caption"
        theme_text_color: "Custom"
        text_color: 1, 1, 1, 0.3
        size_hint_y: None
        height: self.texture_size[1]


# ══════════════════════════════════════
# Telas do App
# ══════════════════════════════════════

MDScreenManager:
    id: sm

    # ────────── TELA 1: Lista de Redes Wi-Fi ──────────
    MDScreen:
        name: "scan"
        md_bg_color: 0.07, 0.07, 0.11, 1

        MDBoxLayout:
            orientation: "vertical"

            MDTopAppBar:
                title: "AcoNuWiFi"
                elevation: 2
                md_bg_color: 0.09, 0.09, 0.14, 1
                specific_text_color: 1, 1, 1, 1
                right_action_items: [["refresh", lambda x: app.refresh_networks()], ["history", lambda x: app.go_to_history()]]

            MDLabel:
                text: "   Toque em uma rede para escanear a senha"
                font_style: "Caption"
                theme_text_color: "Custom"
                text_color: 1, 1, 1, 0.4
                size_hint_y: None
                height: dp(36)

            ScrollView:
                do_scroll_x: False

                MDBoxLayout:
                    id: wifi_list
                    orientation: "vertical"
                    adaptive_height: True
                    spacing: dp(8)
                    padding: [dp(16), dp(4), dp(16), dp(16)]

            MDBoxLayout:
                size_hint_y: None
                height: dp(64)
                padding: [dp(16), dp(8)]
                md_bg_color: 0.09, 0.09, 0.14, 1

                MDRaisedButton:
                    text: "  Digitar Senha Manualmente  "
                    size_hint_x: 1
                    md_bg_color: 0.18, 0.18, 0.25, 1
                    on_release: app.open_manual_entry()


    # ────────── TELA 2: Câmera OCR ──────────
    MDScreen:
        name: "camera"
        md_bg_color: 0, 0, 0, 1

        MDBoxLayout:
            orientation: "vertical"

            MDTopAppBar:
                title: "Aponte para a senha"
                elevation: 0
                md_bg_color: 0, 0, 0, 0.85
                specific_text_color: 1, 1, 1, 1
                left_action_items: [["arrow-left", lambda x: app.go_back_from_camera()]]

            Camera:
                id: camera_widget
                resolution: (640, 480)
                play: False
                allow_stretch: True
                keep_ratio: True

            MDBoxLayout:
                size_hint_y: None
                height: dp(90)
                padding: [dp(16), dp(12)]
                spacing: dp(12)
                md_bg_color: 0, 0, 0, 0.9

                MDLabel:
                    id: ocr_status
                    text: "Pronto para escanear"
                    font_style: "Body2"
                    theme_text_color: "Custom"
                    text_color: 1, 1, 1, 0.6
                    size_hint_x: 0.55

                MDRaisedButton:
                    text: "  CAPTURAR  "
                    size_hint_x: 0.45
                    pos_hint: {"center_y": .5}
                    md_bg_color: app.theme_cls.primary_color
                    on_release: app.capture_and_ocr()


    # ────────── TELA 3: Confirmar e Conectar ──────────
    MDScreen:
        name: "confirm"
        md_bg_color: 0.07, 0.07, 0.11, 1

        MDBoxLayout:
            orientation: "vertical"

            MDTopAppBar:
                title: "Confirmar Conexão"
                elevation: 2
                md_bg_color: 0.09, 0.09, 0.14, 1
                specific_text_color: 1, 1, 1, 1
                left_action_items: [["arrow-left", lambda x: app.go_back_to_scan()]]

            ScrollView:
                do_scroll_x: False

                MDBoxLayout:
                    orientation: "vertical"
                    adaptive_height: True
                    padding: [dp(24), dp(28), dp(24), dp(24)]
                    spacing: dp(16)

                    MDIcon:
                        icon: "wifi-check"
                        halign: "center"
                        font_size: dp(56)
                        theme_text_color: "Custom"
                        text_color: app.theme_cls.primary_color
                        size_hint_y: None
                        height: dp(72)

                    MDLabel:
                        text: "REDE WI-FI"
                        font_style: "Overline"
                        theme_text_color: "Custom"
                        text_color: 1, 1, 1, 0.35
                        size_hint_y: None
                        height: dp(20)

                    MDLabel:
                        id: confirm_ssid
                        text: ""
                        font_style: "H5"
                        theme_text_color: "Custom"
                        text_color: 1, 1, 1, 1
                        bold: True
                        size_hint_y: None
                        height: dp(40)

                    MDLabel:
                        text: "SENHA CAPTURADA (edite se necessário)"
                        font_style: "Overline"
                        theme_text_color: "Custom"
                        text_color: 1, 1, 1, 0.35
                        size_hint_y: None
                        height: dp(20)

                    MDTextField:
                        id: confirm_password
                        hint_text: "Senha do Wi-Fi"
                        mode: "rectangle"
                        size_hint_y: None
                        height: dp(48)

                    MDLabel:
                        id: location_label
                        text: "Localização: obtendo..."
                        font_style: "Caption"
                        theme_text_color: "Custom"
                        text_color: 1, 1, 1, 0.35
                        size_hint_y: None
                        height: dp(24)

                    MDRaisedButton:
                        text: "     CONECTAR AO WI-FI     "
                        size_hint_x: 1
                        size_hint_y: None
                        height: dp(50)
                        md_bg_color: app.theme_cls.primary_color
                        on_release: app.connect_to_wifi()

                    MDRectangleFlatButton:
                        text: "  Escanear Novamente  "
                        size_hint_x: 1
                        size_hint_y: None
                        height: dp(44)
                        text_color: app.theme_cls.primary_color
                        line_color: app.theme_cls.primary_color
                        on_release: app.rescan_password()


    # ────────── TELA 4: Histórico de Redes Salvas ──────────
    MDScreen:
        name: "history"
        md_bg_color: 0.07, 0.07, 0.11, 1

        MDBoxLayout:
            orientation: "vertical"

            MDTopAppBar:
                title: "Redes Salvas"
                elevation: 2
                md_bg_color: 0.09, 0.09, 0.14, 1
                specific_text_color: 1, 1, 1, 1
                left_action_items: [["arrow-left", lambda x: app.go_back_to_scan()]]

            MDCard:
                orientation: "horizontal"
                size_hint_y: None
                height: dp(52)
                padding: [dp(16), dp(10)]
                md_bg_color: 0.12, 0.12, 0.18, 1
                radius: [0]

                MDIcon:
                    icon: "database"
                    theme_text_color: "Custom"
                    text_color: app.theme_cls.primary_color
                    pos_hint: {"center_y": .5}

                MDLabel:
                    id: counter_label
                    text: "Total de redes salvas: 0"
                    font_style: "Subtitle2"
                    theme_text_color: "Custom"
                    text_color: 1, 1, 1, 0.8
                    pos_hint: {"center_y": .5}

            ScrollView:
                do_scroll_x: False

                MDBoxLayout:
                    id: history_list
                    orientation: "vertical"
                    adaptive_height: True
                    spacing: dp(8)
                    padding: [dp(16), dp(12), dp(16), dp(16)]
"""


# ============================================================
# Widgets Customizados (com ButtonBehavior para on_release)
# ============================================================

class WiFiCard(ButtonBehavior, MDCard):
    """Card clicável representando uma rede Wi-Fi disponível."""
    ssid = StringProperty("")
    info_text = StringProperty("")


class HistoryCard(ButtonBehavior, MDCard):
    """Card clicável representando uma rede salva no histórico."""
    network_id = NumericProperty(0)
    ssid = StringProperty("")
    password = StringProperty("")
    saved_info = StringProperty("")


# ============================================================
# App Principal
# ============================================================

class AcoNuWiFiApp(MDApp):
    """Aplicativo AcoNuWiFi — OCR de senhas Wi-Fi."""

    selected_ssid = StringProperty("")
    captured_password = StringProperty("")
    current_lat = NumericProperty(0.0)
    current_lon = NumericProperty(0.0)

    def build(self):
        # Tema escuro com acento Teal
        self.theme_cls.theme_style = "Dark"
        self.theme_cls.primary_palette = "Teal"
        self.theme_cls.primary_hue = "A200"

        # Inicializar serviços
        self.db = WiFiDatabase()
        self.android_svc = AndroidService()
        self.ocr_svc = OCRService()

        # OCR init em background (é leve com ML Kit, mas previne trava na UI)
        threading.Thread(target=self.ocr_svc.initialize, daemon=True).start()

        return Builder.load_string(KV)

    def on_start(self):
        self.android_svc.request_permissions()
        Clock.schedule_once(lambda dt: self.refresh_networks(), 1.0)

    # ═══════════ TELA SCAN ═══════════

    def refresh_networks(self):
        """Atualiza a lista de redes Wi-Fi disponíveis."""
        box = self.root.ids.wifi_list
        box.clear_widgets()

        nets = self.android_svc.scan_wifi()

        if not nets:
            box.add_widget(MDLabel(
                text="Nenhuma rede encontrada.\nVerifique se o Wi-Fi está ativo.",
                halign="center",
                theme_text_color="Custom",
                text_color=(1, 1, 1, 0.4),
                size_hint_y=None,
                height=dp(80),
            ))
            return

        for n in nets:
            level = n["level"]
            sig = (
                "Excelente" if level >= -50 else
                "Bom" if level >= -60 else
                "Médio" if level >= -70 else
                "Fraco"
            )

            cap = n.get("capabilities", "")
            sec = (
                "WPA2" if "WPA2" in cap else
                "WPA" if "WPA" in cap else
                "WEP" if "WEP" in cap else
                "Aberta"
            )

            box.add_widget(WiFiCard(
                ssid=n["ssid"],
                info_text=f"{sec}  •  Sinal {sig}",
            ))

    def on_network_selected(self, ssid):
        """Rede selecionada → abre câmera."""
        self.selected_ssid = ssid
        self.root.ids.sm.current = "camera"
        Clock.schedule_once(lambda dt: self._start_camera(), 0.5)

    def open_manual_entry(self):
        """Abre tela de confirmação para digitar senha manualmente."""
        if not self.selected_ssid:
            self.selected_ssid = "Rede Manual"
        self.captured_password = ""
        self._go_confirm()

    # ═══════════ TELA CÂMERA / OCR ═══════════

    def _start_camera(self):
        try:
            self.root.ids.camera_widget.play = True
            self.root.ids.ocr_status.text = "Câmera ativa. Aponte e capture."
        except Exception as e:
            self.root.ids.ocr_status.text = f"Erro na câmera: {e}"

    def _stop_camera(self):
        try:
            self.root.ids.camera_widget.play = False
        except Exception:
            pass

    def go_back_from_camera(self):
        self._stop_camera()
        self.root.ids.sm.current = "scan"

    def capture_and_ocr(self):
        """Captura frame da câmera e processa OCR em thread separada."""
        self.root.ids.ocr_status.text = "Processando imagem..."

        try:
            if platform == "android":
                from android.storage import app_storage_path
                path = os.path.join(app_storage_path(), "capture.png")
            else:
                path = "capture.png"

            self.root.ids.camera_widget.export_to_png(path)
            threading.Thread(
                target=self._run_ocr, args=(path,), daemon=True
            ).start()
        except Exception as e:
            self.root.ids.ocr_status.text = f"Erro na captura: {e}"

    def _run_ocr(self, path):
        """Executa OCR em background e retorna resultado na thread principal."""
        raw = self.ocr_svc.read_image(path)
        pwd = extract_password(raw)

        def on_done(dt):
            if pwd:
                self.captured_password = pwd
                self.root.ids.ocr_status.text = f"Senha encontrada: {pwd}"
                self._stop_camera()
                Clock.schedule_once(lambda dt2: self._go_confirm(), 0.5)
            else:
                preview = raw[:50] if raw else "nenhum texto"
                self.root.ids.ocr_status.text = (
                    f"Não detectada. Tente novamente.\n"
                    f"Texto: {preview}..."
                )

        Clock.schedule_once(on_done, 0)

    def rescan_password(self):
        """Volta à câmera para tentar novamente."""
        self.root.ids.sm.current = "camera"
        Clock.schedule_once(lambda dt: self._start_camera(), 0.5)

    # ═══════════ TELA CONFIRMAÇÃO ═══════════

    def _go_confirm(self):
        """Navega para a tela de confirmação com dados preenchidos."""
        self.root.ids.confirm_ssid.text = self.selected_ssid
        self.root.ids.confirm_password.text = self.captured_password
        threading.Thread(target=self._fetch_location, daemon=True).start()
        self.root.ids.sm.current = "confirm"

    def _fetch_location(self):
        """Obtém GPS em background."""
        lat, lon = self.android_svc.get_location()
        self.current_lat, self.current_lon = lat, lon

        def update(dt):
            lbl = self.root.ids.location_label
            if lat != 0.0 or lon != 0.0:
                lbl.text = f"📍 {lat:.4f}, {lon:.4f}"
            else:
                lbl.text = "📍 Localização indisponível"

        Clock.schedule_once(update, 0)

    def connect_to_wifi(self):
        """Salva rede no DB e conecta via API nativa Android."""
        ssid = self.root.ids.confirm_ssid.text
        pwd = self.root.ids.confirm_password.text

        if not ssid or not pwd:
            Snackbar(text="Preencha SSID e senha").open()
            return

        # Salvar no banco
        self.db.save(ssid, pwd, self.current_lat, self.current_lon)

        # Conectar em background
        def do_connect():
            ok, msg = self.android_svc.connect(ssid, pwd)
            Clock.schedule_once(
                lambda dt: Snackbar(
                    text=f"{'✅' if ok else '❌'} {msg}"
                ).open(),
                0,
            )

        threading.Thread(target=do_connect, daemon=True).start()
        Snackbar(text="Conectando...").open()

    # ═══════════ TELA HISTÓRICO ═══════════

    def go_to_history(self):
        """Abre a tela de redes salvas."""
        self._load_history()
        self.root.ids.sm.current = "history"

    def _load_history(self):
        """Carrega redes salvas do banco de dados."""
        box = self.root.ids.history_list
        box.clear_widgets()

        count = self.db.count()
        self.root.ids.counter_label.text = f"Total de redes salvas: {count}"

        rows = self.db.get_all()
        if not rows:
            box.add_widget(MDLabel(
                text="Nenhuma rede salva ainda.\nEscaneie senhas para começar!",
                halign="center",
                theme_text_color="Custom",
                text_color=(1, 1, 1, 0.4),
                size_hint_y=None,
                height=dp(60),
            ))
            return

        for net_id, ssid, pwd, lat, lon, saved_at in rows:
            try:
                dt = datetime.fromisoformat(saved_at).strftime("%d/%m/%Y %H:%M")
            except Exception:
                dt = saved_at

            loc = f" • 📍 {lat:.2f},{lon:.2f}" if (lat or lon) else ""

            box.add_widget(HistoryCard(
                network_id=net_id,
                ssid=ssid,
                password=pwd,
                saved_info=f"{dt}{loc}",
            ))

    def on_history_tap(self, net_id, ssid, pwd):
        """Toque em rede salva → abre confirmação para reconectar."""
        self.selected_ssid = ssid
        self.captured_password = pwd
        self._go_confirm()

    # ═══════════ NAVEGAÇÃO ═══════════

    def go_back_to_scan(self):
        self.root.ids.sm.current = "scan"

    def on_stop(self):
        self._stop_camera()
        self.db.close()


# ============================================================
# Ponto de Entrada
# ============================================================

if __name__ == "__main__":
    AcoNuWiFiApp().run()