"""Seguridad de LabelBridge.

- crypto: cifrado AES-256-GCM de los datos en disco (pedidos, etiquetas, configuración). La clave vive en
  el almacén seguro del sistema (Administrador de credenciales de Windows).
- ratelimit: límites de peticiones por minuto y bloqueo tras intentos fallidos.
- policy: la «puerta» por la que pasa toda petición externa (API local, n8n) y toda descarga: aprueba,
  rechaza o limita según los ajustes de seguridad.
- audit: registro de auditoría (seguridad.log) con lo aprobado, rechazado y bloqueado.
- redact: borra claves, tokens y correos de los registros.
"""
