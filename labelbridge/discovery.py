"""Descubrimiento de impresoras en la red local (Wi‑Fi/LAN): escaneo de puertos + mDNS/Bonjour."""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
import threading
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable

from .printing import ipp

log = logging.getLogger(__name__)

PRINTER_PORTS = (9100, 631, 515)
MDNS_TYPES = ("_ipp._tcp.local.", "_ipps._tcp.local.", "_pdl-datastream._tcp.local.", "_printer._tcp.local.")
IPP_PATHS = ("/ipp/print", "/ipp", "/printers/ipp", "/")


@dataclass
class DiscoveredPrinter:
    ip: str
    hostname: str = ""
    name: str = ""
    model: str = ""
    ports: list[int] = field(default_factory=list)
    ipp_path: str = ""
    formats: list[str] = field(default_factory=list)
    sources: list[str] = field(default_factory=list)

    @property
    def protocols(self) -> list[str]:
        names = {9100: "RAW 9100", 631: "IPP", 515: "LPD"}
        return [names.get(p, str(p)) for p in sorted(self.ports)]

    @property
    def is_zpl(self) -> bool:
        text = f"{self.model} {self.name} {self.hostname}".lower()
        return any(word in text for word in ("zebra", "zpl", "zt4", "zd4", "zd6", "gk420", "gx420", "zt2"))

    def suggested(self) -> dict:
        """Configuración recomendada para agregarla a LabelBridge."""
        if 9100 in self.ports and (self.is_zpl or 631 not in self.ports):
            return {"protocol": "raw", "port": 9100, "language": "zpl" if self.is_zpl else "pdf"}
        if 631 in self.ports:
            return {"protocol": "ipp", "port": 631, "language": "pdf", "ipp_path": self.ipp_path or "/ipp/print"}
        return {"protocol": "raw", "port": 9100, "language": "zpl"}

    @property
    def title(self) -> str:
        return self.name or self.model or self.hostname or self.ip


def local_ipv4_addresses() -> list[str]:
    found: list[str] = []
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))  # no envía nada: solo elige la interfaz de salida
            found.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            found.append(info[4][0])
    except OSError:
        pass
    result = []
    for ip in found:
        addr = ipaddress.ip_address(ip)
        if addr.is_loopback or addr.is_link_local or ip in result:
            continue
        result.append(ip)
    return result


def _port_open(ip: str, port: int, timeout: float) -> bool:
    try:
        with socket.create_connection((ip, port), timeout=timeout):
            return True
    except OSError:
        return False


def _http_title(ip: str) -> str:
    try:
        with urllib.request.urlopen(f"http://{ip}/", timeout=1.5) as resp:
            html = resp.read(20000).decode("utf-8", "replace")
    except Exception:
        return ""
    match = re.search(r"<title[^>]*>(.*?)</title>", html, re.IGNORECASE | re.DOTALL)
    return " ".join(match.group(1).split())[:80] if match else ""


def _enrich(printer: DiscoveredPrinter) -> None:
    if not printer.hostname:
        try:
            printer.hostname = socket.gethostbyaddr(printer.ip)[0]
        except OSError:
            pass
    if 631 in printer.ports and not printer.model:
        for path in ([printer.ipp_path] if printer.ipp_path else []) + list(IPP_PATHS):
            try:
                attrs = ipp.get_printer_attributes(printer.ip, 631, path, timeout=2.5)
            except Exception:
                continue
            printer.ipp_path = path
            printer.model = str((attrs.get("printer-make-and-model") or [""])[0])
            printer.name = printer.name or str((attrs.get("printer-info") or attrs.get("printer-name") or [""])[0])
            printer.formats = [str(f) for f in attrs.get("document-format-supported", [])]
            break
    if not printer.model and 9100 in printer.ports:
        # Petición HTTP inofensiva: muchas Zebra/Brother/HP se identifican en su página web.
        title = _http_title(printer.ip)
        if title:
            printer.model = title


def scan_network(
    cancel: threading.Event | None = None,
    progress: Callable[[int, int], None] | None = None,
    timeout: float = 0.35,
) -> list[DiscoveredPrinter]:
    """Escanea la /24 de cada interfaz buscando puertos de impresión (sin enviar datos a 9100)."""
    cancel = cancel or threading.Event()
    hosts: list[str] = []
    for ip in local_ipv4_addresses():
        network = ipaddress.ip_network(f"{ip}/24", strict=False)
        hosts.extend(str(h) for h in network.hosts() if str(h) != ip)
    targets = [(h, p) for h in dict.fromkeys(hosts) for p in PRINTER_PORTS]
    found: dict[str, DiscoveredPrinter] = {}
    lock = threading.Lock()
    done = 0

    def probe(target: tuple[str, int]) -> None:
        nonlocal done
        if cancel.is_set():
            return
        host, port = target
        is_open = _port_open(host, port, timeout)
        with lock:
            done += 1
            if is_open:
                found.setdefault(host, DiscoveredPrinter(ip=host, sources=["escaneo"])).ports.append(port)
            if progress and done % 32 == 0:
                progress(done, len(targets))

    with ThreadPoolExecutor(max_workers=128) as pool:
        list(pool.map(probe, targets))
    if progress:
        progress(len(targets), len(targets))
    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(_enrich, found.values()))
    return sorted(found.values(), key=lambda p: ipaddress.ip_address(p.ip))


def browse_mdns(duration: float = 3.0) -> list[DiscoveredPrinter]:
    try:
        from zeroconf import ServiceBrowser, ServiceStateChange, Zeroconf
    except ImportError:
        return []
    results: dict[str, DiscoveredPrinter] = {}
    lock = threading.Lock()
    zc = Zeroconf()

    def on_change(zeroconf, service_type, name, state_change) -> None:
        if state_change is not ServiceStateChange.Added:
            return
        info = zeroconf.get_service_info(service_type, name, timeout=1500)
        if not info:
            return
        addresses = info.parsed_addresses()
        ipv4 = next((a for a in addresses if ":" not in a), None)
        if not ipv4:
            return
        props = {
            (k.decode() if isinstance(k, bytes) else str(k)): (v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v or ""))
            for k, v in (info.properties or {}).items()
        }
        with lock:
            printer = results.setdefault(ipv4, DiscoveredPrinter(ip=ipv4, sources=["mDNS"]))
            printer.name = printer.name or name.split("._")[0]
            printer.model = printer.model or props.get("ty", "") or props.get("product", "").strip("()")
            printer.hostname = printer.hostname or (info.server or "").rstrip(".")
            if info.port and info.port not in printer.ports:
                printer.ports.append(info.port)
            if service_type.startswith("_ipp") and props.get("rp"):
                printer.ipp_path = "/" + props["rp"].lstrip("/")
            if props.get("pdl"):
                printer.formats = props["pdl"].split(",")

    try:
        browser = ServiceBrowser(zc, list(MDNS_TYPES), handlers=[on_change])
        threading.Event().wait(duration)
        browser.cancel()
    except Exception:
        log.debug("mDNS no disponible", exc_info=True)
    finally:
        zc.close()
    return list(results.values())


def discover(
    cancel: threading.Event | None = None, progress: Callable[[int, int], None] | None = None
) -> list[DiscoveredPrinter]:
    """mDNS + escaneo de puertos, combinados por IP."""
    mdns_result: list[DiscoveredPrinter] = []
    mdns_thread = threading.Thread(target=lambda: mdns_result.extend(browse_mdns()), daemon=True)
    mdns_thread.start()
    scanned = scan_network(cancel, progress)
    mdns_thread.join(6)
    merged = {p.ip: p for p in scanned}
    for printer in mdns_result:
        current = merged.get(printer.ip)
        if current is None:
            merged[printer.ip] = printer
            continue
        current.name = current.name or printer.name
        current.model = printer.model or current.model
        current.hostname = current.hostname or printer.hostname
        current.ipp_path = current.ipp_path or printer.ipp_path
        current.formats = current.formats or printer.formats
        current.ports = sorted(set(current.ports) | set(printer.ports))
        current.sources = sorted(set(current.sources) | set(printer.sources))
    log.info("Descubrimiento de red: %d impresora(s) encontradas", len(merged))
    return sorted(merged.values(), key=lambda p: ipaddress.ip_address(p.ip))
