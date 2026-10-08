"""Pruebas de lectura de barras con imágenes sintéticas."""
import sys, os
import numpy as np
import cv2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from vision import bar_fraction, sample_fill_color

RED = (40, 40, 200)      # BGR
EMPTY = (30, 25, 25)
W, H = 200, 14
rng = np.random.default_rng(0)


def make_bar(frac, text=True, noise=8):
    img = np.full((H, W, 3), EMPTY, np.uint8)
    img[:, : int(round(W * frac))] = RED
    img = np.clip(img.astype(int) + rng.integers(-noise, noise, img.shape), 0, 255).astype(np.uint8)
    if text:
        cv2.putText(img, "1234/1234", (60, 11), cv2.FONT_HERSHEY_PLAIN, 0.8, (255, 255, 255), 1)
    return img


def check(name, got, expected, tol=0.03):
    ok = abs(got - expected) <= tol
    print(f"{'OK ' if ok else 'FAIL'} {name}: {got:.3f} (esperado {expected:.3f})")
    return ok


results = []
fill = sample_fill_color(make_bar(1.0))
print("color calibrado:", fill)

for f in [1.0, 0.75, 0.5, 0.3, 0.1, 0.03]:
    frac, vis = bar_fraction(make_bar(f), fill)
    results.append(check(f"barra {int(f*100)}%", frac, f) and vis)

frac, vis = bar_fraction(make_bar(0.0), fill)
results.append(check("barra vacía", frac, 0.0) and not vis)

# sin target: paisaje del juego (ruido de colores), sin barra
world = rng.integers(0, 255, (H, W, 3), dtype=np.uint8)
frac, vis = bar_fraction(world, fill)
print(f"{'OK ' if not vis else 'FAIL'} paisaje sin barra: visible={vis}")
results.append(not vis)

# paisaje con una mancha roja suelta a la derecha (p.ej. un efecto): no es barra
spot = np.full((H, W, 3), (90, 120, 60), np.uint8)
spot[:, 150:170] = RED
frac, vis = bar_fraction(spot, fill)
print(f"{'OK ' if not vis else 'FAIL'} mancha roja suelta: visible={vis}")
results.append(not vis)

print(f"\n{sum(results)}/{len(results)} pruebas OK")
sys.exit(0 if all(results) else 1)
