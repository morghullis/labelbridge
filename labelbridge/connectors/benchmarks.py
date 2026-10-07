"""Referencias del sector para comparar la tienda (reporte semanal). Cada una con su fuente pública, la fecha en que se
consultó y si es una estimación: nunca se muestra un número sin decir de dónde sale.

Para actualizarlas: cambiar el valor, `checked` y la fuente aquí (y el texto de las novedades). Son promedios de
muchas tiendas (casi siempre más grandes): sirven de orientación, no de meta exacta.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..i18n import N_


@dataclass(frozen=True)
class Benchmark:
    key: str
    title: str  # sin traducir (N_)
    value: float
    unit: str  # money | percent
    source: str  # quién lo publica
    url: str
    checked: str  # cuándo se consultó (AAAA-MM-DD)
    detail: str  # qué mide exactamente (sin traducir, N_)
    estimate: bool = True  # promedio de otras tiendas o encuesta: orientación, no un dato de esta tienda


BENCHMARKS: dict[str, Benchmark] = {
    benchmark.key: benchmark
    for benchmark in (
        Benchmark(
            "aov_jewelry", N_("Ticket promedio en joyería y lujo"), 421.0, "money", "Dynamic Yield",
            "https://marketing.dynamicyield.com/benchmarks/average-order-value/", "2026-10-06",
            N_("Promedio de 12 meses del sector «Luxury & Jewelry» en su panel de comercio electrónico."),
        ),
        Benchmark(
            "aov_global", N_("Ticket promedio de todas las tiendas"), 182.0, "money", "Dynamic Yield",
            "https://marketing.dynamicyield.com/benchmarks/average-order-value/", "2026-10-06",
            N_("Promedio mundial de su panel de comercio electrónico."),
        ),
        Benchmark(
            "repeat", N_("Clientes que vuelven a comprar"), 25.0, "percent", "Little Stream Software",
            "https://www.rivo.io/blog/shopify-customer-retention-benchmarks", "2026-10-06",
            N_("Promedio de más de 1.500 tiendas Shopify (2024; mediana 22,5 %). Cifra de un proveedor, citada por "
               "Rivo."),
        ),
        Benchmark(
            "returns", N_("Ventas en línea que se devuelven"), 19.3, "percent", "NRF y Happy Returns",
            "https://nrf.com/media-center/press-releases/consumers-expected-to-return-nearly-850-billion-in-merchandise-in-2025",
            "2026-10-06",
            N_("Estimación 2025 de comercios grandes de EE. UU. (encuesta). Aquí se compara con lo reembolsado."),
        ),
        Benchmark(
            "conversion_jewelry", N_("Conversión en joyería y lujo"), 0.69, "percent", "Dynamic Yield",
            "https://marketing.dynamicyield.com/benchmarks/conversion-rate/", "2026-10-06",
            N_("Promedio de 12 meses del sector «Luxury & Jewelry» (visitas que terminan en compra)."),
        ),
        Benchmark(
            "cart_abandonment", N_("Carritos abandonados"), 70.22, "percent", "Baymard Institute",
            "https://baymard.com/lists/cart-abandonment-rate", "2026-10-06",
            N_("Promedio de 50 estudios (actualizado el 22 de septiembre de 2025)."),
        ),
    )
}
