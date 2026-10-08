"""Lectura de barras de HP desde la pantalla.

No toca el juego: solo mira píxeles. La región y el color de cada barra
se guardan en la calibración (calibracion.json).
"""
import numpy as np


def sample_fill_color(bar_bgr: np.ndarray) -> list:
    """Color de la barra LLENA: mediana de la franja central de la región."""
    h = bar_bgr.shape[0]
    band = bar_bgr[max(0, h // 2 - h // 4): h // 2 + h // 4 + 1]
    return [int(v) for v in np.median(band.reshape(-1, 3), axis=0)]


def bar_fraction(bar_bgr: np.ndarray, fill_bgr, tol: float = 60.0):
    """Devuelve (fraccion 0..1, visible).

    - fraccion: hasta dónde llega la parte llena, medido desde la izquierda.
    - visible: False si la región no parece una barra (p.ej. no hay target).
    Tolera el texto "1234/1234" que el juego dibuja encima de la barra.
    """
    if bar_bgr is None or bar_bgr.size == 0:
        return 0.0, False
    img = bar_bgr[..., :3].astype(np.int32)
    h, w = img.shape[:2]
    band = img[max(0, h // 2 - h // 4): h // 2 + h // 4 + 1]
    dist = np.linalg.norm(band - np.array(fill_bgr, dtype=np.int32), axis=2)
    col_filled = (dist < tol).mean(axis=0) >= 0.5

    if not col_filled.any():
        return 0.0, False

    last = int(np.nonzero(col_filled)[0].max()) + 1
    density = col_filled[:last].mean()              # la parte llena es casi sólida
    starts_left = col_filled[: max(2, w // 15)].any()  # una barra arranca a la izquierda
    if density < 0.6 or not starts_left:
        return 0.0, False
    return last / w, True
