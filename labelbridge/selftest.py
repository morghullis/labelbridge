"""Autoprueba del ejecutable empaquetado: `LabelBridge.exe --self-test [informe.txt]`.

Comprueba que el paquete trae todo lo necesario (Qt, PDF, interfaz, librerías) sin abrir ventanas
ni tocar los datos del usuario. Devuelve 0 si todo está bien. La usa la compilación en GitHub.
"""

from __future__ import annotations

import os
import sys
import tempfile
import traceback
from pathlib import Path


def run(argv: list[str]) -> int:
    report_path = Path(argv[argv.index("--self-test") + 1]) if len(argv) > argv.index("--self-test") + 1 else (
        Path(tempfile.gettempdir()) / "labelbridge-selftest.txt"
    )
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    os.environ["LABELBRIDGE_HOME"] = tempfile.mkdtemp(prefix="labelbridge-selftest-")
    os.environ["LABELBRIDGE_SECRETS"] = "file"
    lines: list[str] = []
    ok = True

    def check(name: str, fn) -> None:
        nonlocal ok
        try:
            detail = fn()
            lines.append(f"OK    {name}" + (f": {detail}" if detail else ""))
        except Exception:
            ok = False
            lines.append(f"FALLO {name}\n{traceback.format_exc()}")

    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([sys.argv[0]])

    def libraries() -> str:
        import importlib

        for module in ("paho.mqtt.client", "segno", "zeroconf", "PySide6.QtPdf", "PySide6.QtSvg"):
            importlib.import_module(module)
        import certifi
        import keyring

        assert Path(certifi.where()).exists(), "falta el paquete de certificados"
        return f"keyring={type(keyring.get_keyring()).__name__}"

    def windows_printing() -> str:
        if sys.platform != "win32":
            return "no es Windows"
        import win32print

        printers = win32print.EnumPrinters(win32print.PRINTER_ENUM_LOCAL | win32print.PRINTER_ENUM_CONNECTIONS, None, 2)
        return f"{len(printers)} impresora(s) en el sistema"

    def label_pipeline() -> str:
        from . import paths
        from .config import ConfigManager, PrinterProfile
        from .events import Events
        from .jobstore import JobStore
        from .labels.order import sample_order
        from .printing import outputs
        from .printing.engine import Document, PrintEngine
        from .printing.pages import PdfSource

        cfg = ConfigManager(paths.config_path())
        store = JobStore(paths.db_path(), paths.spool_dir())
        engine = PrintEngine(store, cfg, Events())
        profile = PrinterProfile(alias="prueba", kind="file", output_dir=str(paths.output_dir()), media="4x6")
        pdf = engine._as_pdf(Document("order", order=sample_order()), profile)
        assert pdf.startswith(b"%PDF"), "no se generó el PDF"
        source = PdfSource(pdf)
        image = source.render(0, 812, 1218, True)
        source.close()
        assert not image.isNull(), "QtPdf no pudo dibujar el PDF"
        raster_source = PdfSource(pdf)
        zpl = outputs.build_zpl_raster([raster_source], PrinterProfile(language="zpl", media="4x6"), 1)
        raster_source.close()
        assert b"^GFA" in zpl
        store.close()
        return f"PDF {len(pdf) // 1024} KB, ZPL {len(zpl) // 1024} KB"

    def interface(language: str = "es") -> str:
        from . import i18n, paths
        from .config import ConfigManager
        from .events import Events
        from .jobstore import JobStore
        from .printing.engine import PrintEngine
        from .secret_store import SecretStore
        from .services import Services
        from .ui.context import AppContext, QtBridge
        from .ui.main_window import NAV, MainWindow
        from .ui.theme import apply_theme

        apply_theme(app)
        assert i18n.set_language(language) == language, f"no se pudo activar el idioma {language}"
        try:
            cfg = ConfigManager(paths.config_path())
            cfg.data.local_api.enabled = False
            secrets = SecretStore(paths.data_dir() / "secrets.json", backend="file")
            store = JobStore(paths.db_path(), paths.spool_dir())
            events = Events()
            engine = PrintEngine(store, cfg, events)
            services = Services(cfg, secrets, store, engine, events)
            window = MainWindow(AppContext(cfg, secrets, store, engine, services, events, QtBridge(events, cfg)))
            for key in window.pages:
                window.navigate(key)
                app.processEvents()
            assert [key for key, _, _ in NAV if key not in window.pages] == ["assistant"], "faltan pantallas"
            orders = window.nav_buttons["orders"].text().strip()
            assert orders == {"es": "Pedidos", "en": "Orders"}[language], f"pestaña Pedidos: {orders!r}"
            window.quitting = True
            window.close()
            store.close()
        finally:
            i18n.set_language("es")
        return f"{len(window.pages)} pantallas · {len(NAV)} pestañas"

    def languages() -> str:
        """La traducción al inglés viene en el paquete, con sus imágenes de la guía."""
        from . import i18n
        from .paths import resource_path

        catalog = i18n.load_catalog("en")
        assert len(catalog) > 500, f"traducción al inglés incompleta ({len(catalog)} textos)"
        images = list(resource_path("assets", "guide", "en").glob("*.png"))
        assert len(images) >= 8, f"faltan imágenes de la guía en inglés ({len(images)})"
        return f"{len(catalog)} textos en inglés · {len(images)} imágenes de guía en inglés"

    def assistant() -> str:
        """Una conversación completa con cada formato de API (Claude con el SDK empaquetado, OpenAI Responses, Chat
        Completions y Gemini), con servidores simulados: sin red y sin gastar nada."""
        import json
        import threading
        from functools import partial

        import anthropic
        import httpx2

        from . import paths
        from .assistant.agent import DEFAULTS, Conversation
        from .assistant.providers import connect, resolve
        from .assistant.providers.claude import default_sdk_client
        from .assistant.secrets_guard import SecretsGuard
        from .assistant.tools import Toolbox
        from .config import ConfigManager
        from .events import Events
        from .jobstore import JobStore
        from .printing.engine import PrintEngine
        from .secret_store import ANTHROPIC_API_KEY, SecretStore
        from .services import Services
        from .ui.context import AppContext, QtBridge

        cfg = ConfigManager(paths.config_path())
        cfg.data.local_api.enabled = False
        secrets = SecretStore(paths.data_dir() / "secrets.json", backend="file")
        secrets.set(ANTHROPIC_API_KEY, "sk-ant-autoprueba")
        store = JobStore(paths.db_path(), paths.spool_dir())
        events = Events()
        engine = PrintEngine(store, cfg, events)
        ctx = AppContext(cfg, secrets, store, engine, Services(cfg, secrets, store, engine, events), events, QtBridge(events, cfg))

        class SilentUI:
            def confirm(self, *_args) -> bool:
                return False

            def approve(self, *_args) -> str:
                return "once"

            def ask_secret(self, *_args):
                return None

            def open_url(self, _url) -> None:
                pass

            def copy_text(self, _text) -> None:
                pass

            def config_changed(self, _section) -> None:
                pass

        def message(content: list, stop_reason: str) -> dict:
            return {"id": "msg_autoprueba", "type": "message", "role": "assistant", "model": DEFAULTS.model, "content": content,
                    "stop_reason": stop_reason, "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 1}}

        replies = [
            message([{"type": "thinking", "thinking": "", "signature": "s"},
                     {"type": "tool_use", "id": "toolu_1", "name": "add_printer",
                      "input": {"connection": "network", "ip": "127.0.0.1", "alias": "autoprueba"}}], "tool_use"),
            message([{"type": "text", "text": "Listo."}], "end_turn"),
        ]
        sent: list[dict] = []

        def handler(request):
            sent.append(json.loads(request.content))
            return httpx2.Response(200, json=replies.pop(0))

        def factory(api_key: str, base_url: str = ""):
            transport = httpx2.MockTransport(handler)
            return anthropic.Anthropic(api_key=api_key, max_retries=0, http_client=anthropic.DefaultHttpxClient(transport=transport))

        conversation = Conversation(Toolbox(ctx, SilentUI(), SecretsGuard(secrets)), secrets, partial(connect, sdk_factory=factory))
        kinds: list[str] = []
        conversation.send("hola", lambda kind, _data: kinds.append(kind), threading.Event())
        assert kinds[-1] == "reply", f"la conversación terminó con {kinds}"
        assert cfg.find_printer("autoprueba") is not None, "la herramienta no se ejecutó"
        assert sent[1]["messages"][2]["content"][0]["tool_use_id"] == "toolu_1"
        default_sdk_client("sk-ant-autoprueba").close()  # TLS con el almacén de certificados del sistema

        # Los demás formatos, con la misma herramienta y la misma respuesta final.
        args = json.dumps({"connection": "network", "ip": "127.0.0.1", "alias": "autoprueba"})
        scripted = {
            "openai": [
                {"status": "completed", "output": [{"type": "function_call", "id": "fc_1", "call_id": "call_1",
                                                    "name": "set_default_printer", "arguments": json.dumps({"alias": "autoprueba"})}]},
                {"status": "completed", "output": [{"type": "message", "role": "assistant",
                                                    "content": [{"type": "output_text", "text": "Listo."}]}]},
            ],
            "deepseek": [
                {"choices": [{"message": {"role": "assistant", "content": None, "tool_calls": [{"id": "c1", "type": "function",
                              "function": {"name": "add_printer", "arguments": args}}]}, "finish_reason": "tool_calls"}]},
                {"choices": [{"message": {"role": "assistant", "content": "Listo."}, "finish_reason": "stop"}]},
            ],
            "gemini": [
                {"candidates": [{"content": {"role": "model", "parts": [{"functionCall": {"name": "get_status", "args": {}},
                                                                         "thoughtSignature": "c2ln"}]}, "finishReason": "STOP"}]},
                {"candidates": [{"content": {"role": "model", "parts": [{"text": "Listo."}]}, "finishReason": "STOP"}]},
            ],
        }
        for provider_id, replies in scripted.items():
            cfg.data.assistant.provider, cfg.data.assistant.model = provider_id, ""
            secrets.set(resolve(cfg.data.assistant).key_name, "clave-autoprueba")
            requests: list[dict] = []

            def fake(method, url, headers, body, timeout, replies=replies, requests=requests):
                requests.append(json.loads(body))
                return 200, {}, json.dumps(replies.pop(0)).encode("utf-8")

            other = Conversation(Toolbox(ctx, SilentUI(), SecretsGuard(secrets)), secrets, partial(connect, transport=fake))
            kinds = []
            other.send("hola", lambda kind, _data: kinds.append(kind), threading.Event())
            assert kinds[-1] == "reply" and "tool_done" in kinds, f"{provider_id}: la conversación terminó con {kinds}"
            assert len(requests) == 2, f"{provider_id}: {len(requests)} llamadas"
        store.close()
        return f"{len(conversation.tools)} herramientas · Claude, OpenAI, compatibles y Gemini · SDK anthropic {anthropic.__version__}"

    def mascot() -> str:
        """La máscara animada del asistente: todas las expresiones se dibujan y la animación avanza."""
        from PySide6.QtCore import QRectF
        from PySide6.QtGui import QColor, QImage, QPainter

        from .ui import mascot as m

        for key in m.EXPRESSIONS:
            image = QImage(60, 60, QImage.Format.Format_ARGB32)
            image.fill(QColor(0, 0, 0, 0))
            painter = QPainter(image)
            m.paint_mascot(painter, QRectF(0, 0, 60, 60), m.still_frame(key), badge=True)
            painter.end()
            assert image.pixelColor(30, 37).alpha() == 255, f"la expresión {key} no se dibujó"
        face = m.Mascot(reduced_motion=False)
        face.set_base("thinking")
        face.react("happy", 0.5)
        for _ in range(10):
            face.step()
        return f"{len(m.EXPRESSIONS)} expresiones"

    def security() -> str:
        """Cifrado de datos (AES-256-GCM) y política de descargas en el ejecutable empaquetado."""
        from . import paths
        from .config import ConfigManager
        from .jobstore import JobStore
        from .models import JobRequest
        from .secret_store import SecretStore
        from .security.crypto import DataProtector
        from .security.policy import PolicyDenied, SecurityPolicy

        folder = paths.data_dir()
        protector = DataProtector.from_secrets(SecretStore(folder / "secrets-autoprueba.json", backend="file"))
        cfg = ConfigManager(folder / "config-autoprueba.json", protector)
        cfg.save()
        assert b"general" not in (folder / "config-autoprueba.enc").read_bytes(), "la configuración no se cifró"
        store = JobStore(folder / "autoprueba.db", paths.spool_dir(), protector)
        job, _ = store.add(JobRequest(external_id="autoprueba", order={"name": "#1001"}), "test")
        assert store.read_order(job)["name"] == "#1001"
        store.close()
        try:
            SecurityPolicy(cfg).check_url("http://169.254.169.254/latest/meta-data", "api")
        except PolicyDenied:
            pass
        else:
            raise AssertionError("la política de descargas no bloqueó una dirección reservada")
        return "AES-256-GCM · política de seguridad activa"

    def notification_sounds() -> str:
        """Los tonos de aviso se generan y el reproductor de Windows viene en el paquete (sin hacer ruido)."""
        import wave

        from .system import sounds

        folder = Path(tempfile.mkdtemp(prefix="labelbridge-sonidos-"))
        tones = [key for key in sounds.SOUNDS if key not in ("", "windows", "custom")]
        for key in tones:
            with wave.open(str(sounds.tone_file(key, 50, folder))) as wav:
                assert wav.getnframes() > 0, f"el tono {key} salió vacío"
        if sys.platform == "win32":
            import importlib

            importlib.import_module("winsound")
        return f"{len(tones)} tonos"

    def guide_and_updates() -> str:
        from . import updater
        from .paths import resource_path

        images = list(resource_path("assets", "guide").glob("*.png"))
        assert len(images) >= 8, f"faltan imágenes de la guía ({len(images)})"
        assert updater.is_newer("99.0.0") and not updater.is_newer("0.0.1")
        return f"{len(images)} imágenes de guía · actualizador listo"

    check("librerías", libraries)
    check("guía y actualizaciones", guide_and_updates)
    check("impresión de Windows", windows_printing)
    check("etiqueta → PDF → ZPL", label_pipeline)
    check("seguridad", security)
    check("idiomas", languages)
    check("interfaz", interface)
    check("interfaz en inglés", lambda: interface("en"))
    check("asistente", assistant)
    check("máscara animada", mascot)
    check("sonidos de aviso", notification_sounds)
    lines.append("RESULTADO: " + ("OK" if ok else "FALLO"))
    report_path.write_text("\n".join(lines), encoding="utf-8")
    return 0 if ok else 1
