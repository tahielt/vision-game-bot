"""Prueba de punta a punta de agent.py sin Windows.

Reemplaza pygetwindow / mss / pydirectinput / pynput por versiones falsas que
dibujan las barras de HP del juego simulado como PÍXELES, y traducen las teclas
apretadas a acciones del juego simulado. Así se prueba el camino completo:
pantalla → visión → cerebro → teclas.
"""
import os, sys, json, random, tempfile, threading, types, time as real_time
import numpy as np
import cv2

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [HERE, os.path.join(HERE, "..")]
from test_sim import World

SIM_MINUTES = 40
world = World(seed=3, hard=False, gradual_potions=True)
cfg = json.load(open(os.path.join(HERE, "..", "config.json"), encoding="utf-8"))
cfg["ventana_titulo"] = "My Game"
KEY2ACTION = {v: k for k, v in cfg["teclas"].items()}
pressed = []

# ---------- pantalla falsa ----------
W, H = 1024, 768
MY = [40, 30, 220, 14]
TG = [420, 30, 220, 14]
RED, EMPTY = (40, 40, 200), (30, 25, 25)
bg = np.random.default_rng(1).integers(0, 255, (H, W, 3), dtype=np.uint8)


def draw_bar(img, region, frac):
    x, y, w, h = region
    img[y:y + h, x:x + w] = EMPTY
    img[y:y + h, x:x + int(round(w * frac))] = RED
    cv2.putText(img, "123/456", (x + 70, y + 11), cv2.FONT_HERSHEY_PLAIN, 0.8, (255, 255, 255), 1)


def render():
    img = bg.copy()
    draw_bar(img, MY, world.hp / world.max_hp)
    if world.target is not None and world.target.hp > 0:
        draw_bar(img, TG, world.target.hp / world.target.max)
    return img


class FakeWin:
    title, left, top, width, height, _hWnd = "My Game - Hero", 0, 0, W, H, 1
    def activate(self): pass


class FakeSct:
    def __enter__(self): return self
    def __exit__(self, *a): return False
    def grab(self, r):
        if (r["left"], r["top"]) == (MY[0], MY[1]):   # una lectura de mi HP = un paso del juego
            world.step()
        img = render()[r["top"]:r["top"] + r["height"], r["left"]:r["left"] + r["width"]]
        return np.dstack([img, np.full(img.shape[:2], 255, np.uint8)])   # BGRA como mss


listener_cb = {}
Key = types.SimpleNamespace(f9="F9", f10="F10")


class FakeListener:
    def __init__(self, on_press, daemon=True): listener_cb["fn"] = on_press
    def start(self): pass


def fake_press(key):
    pressed.append(key)
    world.press(KEY2ACTION[key])


sys.modules["pygetwindow"] = types.SimpleNamespace(getAllWindows=lambda: [FakeWin()])
sys.modules["mss"] = types.SimpleNamespace(mss=FakeSct)
# el bot ahora mantiene la tecla: keyDown → espera → keyUp (cuenta como una presión)
sys.modules["pydirectinput"] = types.SimpleNamespace(keyDown=fake_press, keyUp=lambda k: None, PAUSE=0)
sys.modules["pynput"] = types.SimpleNamespace(keyboard=types.SimpleNamespace(Key=Key, Listener=FakeListener))
sys.modules["pynput.keyboard"] = sys.modules["pynput"].keyboard

import agent  # noqa: E402

tmp = tempfile.mkdtemp()
agent.CONFIG_PATH = os.path.join(tmp, "config.json")
agent.CALIB_PATH = os.path.join(tmp, "calibracion.json")
agent.LOG_PATH = os.path.join(tmp, "agent.log")
agent.DEBUG_DIR = os.path.join(tmp, "debug")
cfg["tick_s"] = 0
json.dump(cfg, open(agent.CONFIG_PATH, "w"))

# calibración: lo mismo que hace calibrate() pero sin ventana gráfica
world.target = None
full = render()
from vision import sample_fill_color  # noqa: E402
x, y, w, h = MY
calib = {"mi_hp": {"region": MY, "color": sample_fill_color(full[y:y + h, x:x + w])},
         "target_hp": {"region": TG, "color": sample_fill_color(full[y:y + h, x:x + w])}}
json.dump(calib, open(agent.CALIB_PATH, "w"))

agent.is_foreground = lambda w: True
agent.time = types.SimpleNamespace(monotonic=lambda: world.t, sleep=lambda s: None, time=lambda: world.t, strftime=real_time.strftime)
sys.argv = ["agent"]


def driver():
    while "fn" not in listener_cb:
        real_time.sleep(0.01)
    listener_cb["fn"]("F9")                          # el usuario aprieta F9
    deadline = real_time.time() + 120
    while world.t < SIM_MINUTES * 60 and not world.dead and not world.escaped and real_time.time() < deadline:
        real_time.sleep(0.05)
    listener_cb["fn"]("F10")                         # y después F10


threading.Thread(target=driver, daemon=True).start()
agent.main()

min_kills = SIM_MINUTES * 2          # la simulación directa hace ~3 por minuto con loot
ok = ((not world.dead) and world.t >= SIM_MINUTES * 60 * 0.99 and world.kills >= min_kills
      and cfg["lootear"] and world.loot_rate() >= 0.80 and world.foreign_picked == 0)
actions = {a: sum(1 for k in pressed if KEY2ACTION[k] == a) for a in cfg["teclas"]}
print(f"\nminutos simulados: {world.t/60:.0f} | vivo: {not world.dead} | escapó: {world.escaped} | "
      f"mobs matados: {world.kills} (mínimo {min_kills}) | loot levantado: "
      f"{world.items_picked}/{world.own_drops_created} ({world.loot_rate():.0%})\nteclas apretadas: {actions}")
# diagnóstico: el log tiene una línea de estado por segundo y cada tecla; hay capturas en debug/
logtxt = open(agent.LOG_PATH, encoding="utf-8").read()
n_estado, n_tecla = logtxt.count("ESTADO vida"), logtxt.count("TECLA ")
shots = os.listdir(agent.DEBUG_DIR) if os.path.isdir(agent.DEBUG_DIR) else []
diag_ok = n_estado > 100 and n_tecla > 100 and any(f.endswith("_pantalla.jpg") for f in shots)
print(f"diagnóstico: {n_estado} líneas de estado, {n_tecla} teclas registradas, {len(shots)} capturas en debug/")
print("  ej.:", next(l for l in logtxt.splitlines() if "ESTADO vida" in l))
ok &= diag_ok
print("SMOKE OK" if ok else "SMOKE FAIL")
sys.exit(0 if ok else 1)
