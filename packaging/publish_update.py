"""Publica una versión en la página pública de descargas (GitHub Pages de labelbridge-updates).

Genera: index.html (página de descarga), latest.json (lo consulta la app), el instalador y el icono.
Con --push sube todo a la rama gh-pages usando la llave de despliegue UPDATES_DEPLOY_KEY.
Solo se conserva la última versión (un único commit): el repositorio no crece con cada publicación.

Uso (lo ejecuta GitHub Actions al crear un tag vX.Y.Z):  python packaging/publish_update.py --push
"""

from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BASE_URL = "https://morghullis.github.io/labelbridge-updates/"
UPDATES_REPO = "git@github.com:morghullis/labelbridge-updates.git"
# Llave pública oficial de GitHub (api.github.com/meta): no se acepta ningún otro servidor.
GITHUB_HOST_KEY = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"


def app_version() -> str:
    text = (ROOT / "labelbridge" / "__init__.py").read_text(encoding="utf-8")
    return re.search(r'__version__ = "([^"]+)"', text).group(1)


def changelog_notes(version: str) -> str:
    lines = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8").splitlines()
    notes, capturing = [], False
    for line in lines:
        if line.startswith("## "):
            if capturing:
                break
            capturing = line[3:].strip().split(" ")[0] == version
            continue
        if capturing:
            notes.append(line)
    text = "\n".join(notes).strip()
    if not text:
        raise SystemExit(f"CHANGELOG.md no tiene notas para la versión {version}")
    return text


def notes_html(notes: str) -> str:
    items = [f"<li>{html.escape(line[2:].strip())}</li>" for line in notes.splitlines() if line.strip().startswith(("- ", "* "))]
    return "<ul>" + "".join(items) + "</ul>" if items else f"<p>{html.escape(notes)}</p>"


PAGE = """<!doctype html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>LabelBridge · Descargar para Windows</title>
<meta name="description" content="LabelBridge imprime automáticamente las etiquetas de los pedidos de Shopify en Windows.">
<link rel="icon" href="icon.png">
<style>
  :root {{ --bg: #0b0907; --panel: #15110c; --line: #2b2318; --text: #f6f0e3; --muted: #b9ab90;
           --gold1: #fcebbd; --gold2: #d9ae55; --gold3: #8e6522; }}
  * {{ box-sizing: border-box; }}
  body {{ margin: 0; font-family: "Segoe UI", system-ui, -apple-system, Roboto, Arial, sans-serif; color: var(--text);
         background: radial-gradient(1200px 600px at 50% -10%, #3a2c17 0%, var(--bg) 60%) fixed, var(--bg); }}
  main {{ max-width: 920px; margin: 0 auto; padding: 56px 20px 72px; }}
  .hero {{ display: grid; grid-template-columns: 168px 1fr; gap: 36px; align-items: center; }}
  .hero img {{ width: 168px; height: 168px; filter: drop-shadow(0 18px 40px rgba(217, 174, 85, .25)); }}
  .brand {{ letter-spacing: .32em; text-transform: uppercase; font-size: 12px; color: var(--gold2); font-weight: 600; }}
  h1 {{ margin: 8px 0 10px; font-size: 44px; line-height: 1.05;
        background: linear-gradient(120deg, var(--gold1), var(--gold2) 45%, var(--gold3) 70%, var(--gold1));
        -webkit-background-clip: text; background-clip: text; color: transparent; }}
  .lead {{ color: var(--muted); font-size: 18px; line-height: 1.5; margin: 0 0 26px; }}
  .download {{ display: inline-flex; align-items: center; gap: 12px; padding: 16px 26px; border-radius: 14px;
               font-size: 18px; font-weight: 700; color: #1b1408; text-decoration: none;
               background: linear-gradient(135deg, var(--gold1), var(--gold2) 55%, #b5862f);
               box-shadow: 0 12px 30px rgba(217, 174, 85, .28); transition: transform .15s ease, box-shadow .15s ease; }}
  .download:hover {{ transform: translateY(-2px); box-shadow: 0 16px 36px rgba(217, 174, 85, .38); }}
  .meta {{ margin-top: 12px; color: var(--muted); font-size: 14px; }}
  .grid {{ display: grid; grid-template-columns: repeat(3, 1fr); gap: 16px; margin: 48px 0 18px; }}
  .card {{ background: linear-gradient(180deg, #1a140d, var(--panel)); border: 1px solid var(--line);
           border-radius: 16px; padding: 20px; }}
  .num {{ width: 32px; height: 32px; border-radius: 50%; display: grid; place-items: center; font-weight: 800;
          color: #1b1408; background: linear-gradient(135deg, var(--gold1), var(--gold2)); margin-bottom: 12px; }}
  .card h3 {{ margin: 0 0 6px; font-size: 16px; }}
  .card p {{ margin: 0; color: var(--muted); font-size: 14px; line-height: 1.5; }}
  section.notes {{ margin-top: 28px; }}
  section.notes h2 {{ font-size: 20px; margin: 0 0 12px; color: var(--gold1); }}
  section.notes ul {{ margin: 0; padding-left: 20px; line-height: 1.7; color: var(--text); }}
  .auto {{ margin-top: 26px; padding: 16px 18px; border-radius: 14px; border: 1px dashed #5b4824; color: var(--muted);
           font-size: 14px; line-height: 1.5; }}
  footer {{ margin-top: 40px; color: #7d705a; font-size: 12px; line-height: 1.7; word-break: break-all; }}
  footer a {{ color: var(--gold2); }}
  @media (max-width: 720px) {{ .hero {{ grid-template-columns: 1fr; text-align: center; }}
    .hero img {{ margin: 0 auto; width: 128px; height: 128px; }} h1 {{ font-size: 34px; }} .grid {{ grid-template-columns: 1fr; }} }}
</style>
</head>
<body>
<main>
  <div class="hero">
    <img src="icon.png" alt="Icono de LabelBridge">
    <div>
      <div class="brand">Pedro Yoruba Jewelry</div>
      <h1>LabelBridge</h1>
      <p class="lead">Imprime solas las etiquetas de los pedidos de Shopify en tu PC con Windows: impresoras USB, de red o Wi‑Fi.</p>
      <a class="download" href="{installer}" download>⬇&nbsp; Descargar para Windows</a>
      <div class="meta">Versión {version} · {size} · Windows 10/11 de 64 bits · Publicada el {date}</div>
    </div>
  </div>

  <div class="grid">
    <div class="card"><div class="num">1</div><h3>Descarga e instala</h3>
      <p>Abre el archivo descargado. Si Windows muestra «Windows protegió su PC», pulsa <b>Más información → Ejecutar de todas formas</b>.</p></div>
    <div class="card"><div class="num">2</div><h3>Sigue la guía</h3>
      <p>La app se abre en la <b>Guía paso a paso</b>: impresora, prueba, tienda de Shopify y pedido de prueba.</p></div>
    <div class="card"><div class="num">3</div><h3>Listo</h3>
      <p>Cada pedido pagado se imprime solo. La app vive junto al reloj y arranca con Windows.</p></div>
  </div>

  <section class="notes">
    <h2>Novedades de la versión {version}</h2>
    {notes}
  </section>

  <div class="auto">¿Ya la tienes instalada? No necesitas volver a esta página: LabelBridge te avisa cuando hay una versión nueva y se actualiza con un clic, conservando tu configuración.</div>

  <footer>
    SHA-256 del instalador: {sha256}<br>
    Información de versión para la app: <a href="latest.json">latest.json</a>
  </footer>
</main>
</body>
</html>
"""


def build_site(out: Path) -> dict:
    version = app_version()
    installer = ROOT / "dist" / "installer" / f"LabelBridge-Setup-{version}.exe"
    if not installer.exists():
        raise SystemExit(f"No existe {installer}")
    data = installer.read_bytes()
    sha256 = hashlib.sha256(data).hexdigest()
    notes = changelog_notes(version)
    published = datetime.now(timezone.utc).replace(microsecond=0)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    shutil.copy2(installer, out / installer.name)
    shutil.copy2(ROOT / "assets" / "icon.png", out / "icon.png")
    (out / ".nojekyll").write_text("", encoding="utf-8")
    manifest = {
        "version": version,
        "published_at": published.isoformat().replace("+00:00", "Z"),
        "notes": notes,
        "page_url": BASE_URL,
        "installer": {"url": BASE_URL + installer.name, "size": len(data), "sha256": sha256},
    }
    (out / "latest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    (out / "index.html").write_text(
        PAGE.format(
            installer=installer.name,
            version=html.escape(version),
            size=f"{len(data) / 1024 / 1024:.1f} MB".replace(".", ","),
            date=published.strftime("%d/%m/%Y"),
            notes=notes_html(notes),
            sha256=sha256,
        ),
        encoding="utf-8",
    )
    print(f"Sitio generado en {out} (versión {version}, {len(data)} bytes)")
    return manifest


def push(out: Path, version: str) -> None:
    key = os.environ.get("UPDATES_DEPLOY_KEY", "").strip()
    if not key:
        raise SystemExit("Falta el secreto UPDATES_DEPLOY_KEY")
    work = Path(tempfile.mkdtemp(prefix="labelbridge-publish-"))
    key_file, hosts_file = work / "id_ed25519", work / "known_hosts"
    try:
        key_file.write_text(key + "\n", encoding="utf-8")
        os.chmod(key_file, stat.S_IRUSR | stat.S_IWUSR)
        hosts_file.write_text(GITHUB_HOST_KEY + "\n", encoding="utf-8")
        env = {
            **os.environ,
            "GIT_SSH_COMMAND": (
                f"ssh -i {key_file} -o IdentitiesOnly=yes -o StrictHostKeyChecking=yes "
                f"-o UserKnownHostsFile={hosts_file}"
            ),
        }

        def git(*args: str) -> None:
            subprocess.run(["git", *args], cwd=out, env=env, check=True)

        git("init", "-q", "-b", "gh-pages")
        git("add", "-A")
        git("-c", "user.name=LabelBridge CI", "-c", "user.email=ci@labelbridge.local", "commit", "-q", "-m", f"LabelBridge {version}")
        git("push", "--force", "-q", UPDATES_REPO, "gh-pages")
        print(f"Publicado en {BASE_URL}")
    finally:
        shutil.rmtree(work, ignore_errors=True)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", default=str(ROOT / "build" / "site"))
    parser.add_argument("--push", action="store_true")
    args = parser.parse_args()
    tag = os.environ.get("GITHUB_REF_NAME", "")
    version = app_version()
    if tag and tag != f"v{version}":
        raise SystemExit(f"El tag {tag} no coincide con la versión de la app ({version})")
    out = Path(args.out)
    build_site(out)
    if args.push:
        push(out, version)


if __name__ == "__main__":
    sys.exit(main())
