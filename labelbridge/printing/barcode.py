"""Code 128 (subconjuntos B y C) para dibujar códigos de barras sin dependencias."""

from __future__ import annotations

# Anchos barra/espacio de cada símbolo 0..106 (106 = STOP, 7 elementos).
PATTERNS = (
    "212222", "222122", "222221", "121223", "121322", "131222", "122213", "122312", "132212", "221213",
    "221312", "231212", "112232", "122132", "122231", "113222", "123122", "123221", "223211", "221132",
    "221231", "213212", "223112", "312131", "311222", "321122", "321221", "312212", "322112", "322211",
    "212123", "212321", "232121", "111323", "131123", "131321", "112313", "132113", "132311", "211313",
    "231113", "231311", "112133", "112331", "132131", "113123", "113321", "133121", "313121", "211331",
    "231131", "213113", "213311", "213131", "311123", "311321", "331121", "312113", "312311", "332111",
    "314111", "221411", "431111", "111224", "111422", "121124", "121421", "141122", "141221", "112214",
    "112412", "122114", "122411", "142112", "142211", "241211", "221114", "413111", "241112", "134111",
    "111242", "121142", "121241", "114212", "124112", "124211", "411212", "421112", "421211", "212141",
    "214121", "412121", "111143", "111341", "131141", "114113", "114311", "411113", "411311", "113141",
    "114131", "311141", "411131", "211412", "211214", "211232", "2331112",
)

START_B = 104
START_C = 105
STOP = 106


def _codes(data: str) -> list[int]:
    if len(data) >= 2 and len(data) % 2 == 0 and data.isdigit():
        values = [START_C] + [int(data[i : i + 2]) for i in range(0, len(data), 2)]
    else:
        values = [START_B]
        for ch in data:
            code = ord(ch)
            values.append(code - 32 if 32 <= code <= 127 else ord("?") - 32)
    checksum = values[0] + sum(i * v for i, v in enumerate(values[1:], start=1))
    values.append(checksum % 103)
    values.append(STOP)
    return values


def code128_widths(data: str) -> list[int]:
    """Lista de anchos (en módulos) alternando barra, espacio, barra... empezando por barra."""
    widths: list[int] = []
    for value in _codes(data):
        widths.extend(int(c) for c in PATTERNS[value])
    return widths


def code128_modules(data: str) -> str:
    """Representación '1' (barra) / '0' (espacio), útil para pruebas."""
    out = []
    for i, w in enumerate(code128_widths(data)):
        out.append(("1" if i % 2 == 0 else "0") * w)
    return "".join(out)
