"""Vision Desktop Agent — agente que juega un MMO leyendo la pantalla.

Mira la pantalla (barras de HP) y aprieta teclas de la barra de atajos del juego,
como lo haría una persona. No modifica ni lee la memoria del juego.
Levanta el loot con la acción "Pick Up" del juego después de cada kill.

Uso:
    py agent.py              → espera a que abras el juego; F9 inicia/pausa, F10 sale
    py agent.py --calibrar   → vuelve a marcar las barras de HP en pantalla
    py agent.py --ventanas   → lista los títulos de ventanas abiertas (para config.json)
"""
import ctypes
import json
import logging
import os
import random
import sys
import threading
import time

import numpy as np

from brain import Brain, RUNNING
from vision import bar_fraction, sample_fill_color

VERSION = "1.0"
APP_DIR = os.path.dirname(sys.executable if getattr(sys, "frozen", False) else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
CALIB_PATH = os.path.join(APP_DIR, "calibracion.json")
LOG_PATH = os.path.join(APP_DIR, "agent.log")

DEBUG_DIR = os.path.join(APP_DIR, "debug")

log = logging.getLogger("agent")
diag = logging.getLogger("agent.diag")    # detalle fino: solo al archivo, no a la pantalla


def setup_logging():
    log.setLevel(logging.INFO)
    fmt = logging.Formatter("%(asctime)s  %(message)s", "%H:%M:%S")
    fh = logging.FileHandler(LOG_PATH, encoding="utf-8")
    fh.setFormatter(fmt)
    sh = logging.StreamHandler(sys.stdout)
    sh.setFormatter(fmt)
    log.addHandler(sh)
    log.addHandler(fh)
    diag.setLevel(logging.INFO)
    diag.propagate = False
    diag.addHandler(fh)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------- ventana del juego
def make_dpi_aware():
    """Sin esto, con escala de Windows al 125%/150% las coordenadas no coinciden."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:
            pass


# Nombres alternativos de la ventana del juego (config.json: "ventana_titulos_conocidos")
KNOWN_TITLES = []
PICK_AFTER_S = 8          # si no lo encuentra en estos segundos, ofrece elegir de una lista


def visible_windows():
    """Ventanas abiertas, con nombre y de tamaño de juego (las minimizadas no cuentan)."""
    import pygetwindow as gw
    return [w for w in gw.getAllWindows()
            if (w.title or "").strip() and w.width > 300 and w.height > 300]


def find_game(cfg):
    """Devuelve (ventana, candidatas). Ventana = None si no hay una sola candidata clara."""
    wins = visible_windows()
    want = (cfg.get("ventana_titulo") or "").strip().lower()
    hits = [w for w in wins if want and want in w.title.lower()]
    if not hits:
        hits = [w for w in wins if any(k.lower() in w.title.lower() for k in cfg.get("ventana_titulos_conocidos", KNOWN_TITLES))]
    unique = {w.title: w for w in hits}
    return (next(iter(unique.values())) if len(unique) == 1 else None), list(unique.values())


def save_window_title(cfg, title):
    """Guarda el nombre de la ventana en config.json para no volver a preguntar."""
    cfg["ventana_titulo"] = title
    try:
        data = load_json(CONFIG_PATH)
        data["ventana_titulo"] = title
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        log.info(f"Guardado en config.json: ventana_titulo = '{title}'")
    except Exception as e:
        log.info(f"No se pudo guardar config.json ({e}); se usa solo en esta sesión.")


def pick_window(cfg, candidates=None):
    """Lista las ventanas abiertas y deja elegir la del juego con un número."""
    wins = candidates or visible_windows()
    if not wins:
        print("\nNo hay ventanas abiertas de tamaño de juego. ¿El juego está abierto y NO minimizado?")
        return None
    print("\n¿Cuál es la ventana del juego?")
    for i, w in enumerate(wins, 1):
        print(f"  {i}. {w.title}")
    try:
        ans = input("Escribí el número y Enter (solo Enter = seguir esperando): ").strip()
    except EOFError:
        return None
    if ans.isdigit() and 1 <= int(ans) <= len(wins):
        w = wins[int(ans) - 1]
        save_window_title(cfg, w.title)
        return w
    return None


def wait_for_game(cfg):
    log.info(f"Buscando la ventana del juego ('{cfg.get('ventana_titulo', '')}')...")
    t0 = time.time()
    while True:
        w, cands = find_game(cfg)
        if w:
            log.info(f"Juego encontrado: '{w.title}'")
            if (cfg.get("ventana_titulo") or "").lower() not in w.title.lower():
                # guarda la parte fija ("MiJuego"), no el nombre del personaje ("MiJuego - Personaje")
                key = next((k for k in cfg.get("ventana_titulos_conocidos", KNOWN_TITLES) if k.lower() in w.title.lower()), w.title)
                save_window_title(cfg, key)
            return w
        if len(cands) > 1:                      # más de una ventana parecida: que elija
            w = pick_window(cfg, cands)
            if w:
                return w
            t0 = time.time()
        elif time.time() - t0 >= PICK_AFTER_S:
            print("\nNo encuentro la ventana del juego. Abrí el juego (que no quede minimizado).")
            w = pick_window(cfg)
            if w:
                return w
            t0 = time.time()
        time.sleep(2)


def is_foreground(win):
    return ctypes.windll.user32.GetForegroundWindow() == win._hWnd


def grab(sct, win, region):
    """region = [x, y, ancho, alto] relativo a la esquina de la ventana."""
    x, y, w, h = region
    shot = sct.grab({"left": win.left + x, "top": win.top + y, "width": w, "height": h})
    return np.array(shot)[..., :3]   # BGRA → BGR


# ---------------------------------------------------------------- calibración
def calibrate(cfg):
    import cv2
    import mss

    print("\n=== CALIBRACIÓN ===")
    win = wait_for_game(cfg)
    print("\n1. Entrá al juego con el personaje CON LA VIDA LLENA.")
    print("2. Seleccioná (click) un mob que tenga la vida llena, para que se vea su barra.")
    print("3. Volvé a esta ventana y apretá Enter.")
    input()
    win.activate()
    time.sleep(1.5)
    with mss.mss() as sct:
        img = grab(sct, win, [0, 0, win.width, win.height]).copy()

    regions = {}
    for key, msg in (("mi_hp", "Marcá TU barra de HP (solo la parte roja) y apretá ENTER"),
                     ("target_hp", "Marcá la barra de HP del MOB seleccionado y apretá ENTER")):
        cv2.namedWindow(msg, cv2.WINDOW_NORMAL)
        x, y, w, h = cv2.selectROI(msg, img, showCrosshair=False)
        cv2.destroyWindow(msg)
        if w < 10 or h < 2:
            print("Selección inválida, volvé a correr la calibración.")
            sys.exit(1)
        crop = img[y:y + h, x:x + w]
        try:                                   # para revisar después si la barra se lee mal
            os.makedirs(DEBUG_DIR, exist_ok=True)
            cv2.imwrite(os.path.join(DEBUG_DIR, f"calibracion_{key}.png"), crop)
        except Exception:
            pass
        color = sample_fill_color(crop)
        frac, vis = bar_fraction(crop, color, cfg["tolerancia_color"])
        regions[key] = {"region": [int(x), int(y), int(w), int(h)], "color": color}
        print(f"  {key}: región={regions[key]['region']} color={color} lectura={frac:.0%}")
        if not vis or frac < 0.9:
            print("  ⚠ La barra no se lee como llena. ¿Estaba la vida al 100%? Probá de nuevo.")

    with open(CALIB_PATH, "w", encoding="utf-8") as f:
        json.dump(regions, f, indent=2)
    print(f"\nCalibración guardada en {CALIB_PATH}\n")


# ---------------------------------------------------------------- IO real
class WinIO:
    """Lo que el cerebro necesita: leer barras y apretar teclas."""

    MAX_SNAPSHOTS = 30

    def __init__(self, cfg, calib, win, sct):
        import pydirectinput
        pydirectinput.PAUSE = 0.0
        self.pdi = pydirectinput
        self.cfg, self.calib, self.win, self.sct = cfg, calib, win, sct
        self.last = {}                          # última lectura de cada barra (para el diagnóstico)
        self.snapshots = 0

    def _read(self, key):
        c = self.calib[key]
        img = grab(self.sct, self.win, c["region"])
        self.last[key] = img
        return bar_fraction(img, c["color"], self.cfg["tolerancia_color"])

    def read_my_hp(self):
        return self._read("mi_hp")

    def read_target(self):
        return self._read("target_hp")

    def press(self, action):
        """Mantiene la tecla apretada ~80 ms, como una persona.

        Un apretar/soltar instantáneo a veces no lo registra el juego (Unreal lee el
        teclado una vez por cuadro): era la causa de "a veces ataca y a veces no".
        """
        key = self.cfg["teclas"].get(action)
        if not key or not is_foreground(self.win):     # nunca manda teclas a otra ventana
            return
        hold = self.cfg.get("tecla_duracion_ms", 80) / 1000.0
        self.pdi.keyDown(key)
        time.sleep(hold * random.uniform(0.9, 1.3))
        self.pdi.keyUp(key)
        time.sleep(self.cfg.get("tecla_pausa_ms", 40) / 1000.0)
        diag.info(f"TECLA {key.upper():4} {action}")

    def snapshot(self, reason):
        """Guarda las barras y la ventana del juego en debug/ cuando algo no cierra."""
        if self.snapshots >= self.MAX_SNAPSHOTS:
            return
        try:
            import cv2
            os.makedirs(DEBUG_DIR, exist_ok=True)
            self.snapshots += 1
            stamp = time.strftime("%H%M%S") + f"_{self.snapshots:02d}_{reason}"
            for key, img in self.last.items():
                cv2.imwrite(os.path.join(DEBUG_DIR, f"{stamp}_{key}.png"), img)
            full = grab(self.sct, self.win, [0, 0, self.win.width, self.win.height])
            cv2.imwrite(os.path.join(DEBUG_DIR, f"{stamp}_pantalla.jpg"), full,
                        [cv2.IMWRITE_JPEG_QUALITY, 70])
            diag.info(f"CAPTURA debug/{stamp}_*")
        except Exception as e:
            diag.info(f"no se pudo guardar captura: {e}")


# ---------------------------------------------------------------- hotkeys
class Controls:
    def __init__(self, cfg):
        from pynput import keyboard
        self.active = False
        self.quit = threading.Event()
        start_key = getattr(keyboard.Key, cfg["hotkey_iniciar_pausar"])
        quit_key = getattr(keyboard.Key, cfg["hotkey_salir"])

        def on_press(k):
            if k == start_key:
                self.active = not self.active
                log.info("▶ BOT ACTIVO" if self.active else "⏸ BOT EN PAUSA")
            elif k == quit_key:
                log.info("Saliendo...")
                self.quit.set()
                return False

        keyboard.Listener(on_press=on_press, daemon=True).start()


def beep():
    try:
        import winsound
        for _ in range(3):
            winsound.Beep(880, 300)
    except Exception:
        pass


# ---------------------------------------------------------------- main
def main():
    make_dpi_aware()
    setup_logging()
    cfg = load_json(CONFIG_PATH)

    if "--ventanas" in sys.argv:          # para saber qué poner en "ventana_titulo"
        import pygetwindow as gw
        print("\nVentanas abiertas (buscá la del juego y copiá su nombre):\n")
        for t in sorted({w.title for w in gw.getAllWindows() if w.title}):
            print(" -", t)
        return

    if "--calibrar" in sys.argv or not os.path.exists(CALIB_PATH):
        calibrate(cfg)
    calib = load_json(CALIB_PATH)

    import mss
    win = wait_for_game(cfg)
    controls = Controls(cfg)
    log.info(f"Vision Desktop Agent v{VERSION}")
    log.info(f"Listo. Poné al personaje en la zona de farm y apretá {cfg['hotkey_iniciar_pausar'].upper()} "
             f"para iniciar/pausar. {cfg['hotkey_salir'].upper()} para salir.")

    t0 = time.time()
    with mss.mss() as sct:
        io = WinIO(cfg, calib, win, sct)
        brain = Brain(cfg, io, clock=time.monotonic, log=log.info)
        was_active, warned_bg = False, False
        last_diag = 0.0

        while not controls.quit.is_set():
            if not controls.active:
                was_active = False
                time.sleep(0.2)
                continue
            if not was_active:
                brain.reset()               # arranca de cero cada vez que se reanuda
                was_active = True
            if not is_foreground(win):
                if not warned_bg:
                    log.info("El juego no está en primer plano → en espera (no manda teclas).")
                    warned_bg = True
                time.sleep(0.5)
                continue
            warned_bg = False

            status = brain.tick()
            if time.monotonic() - last_diag >= 1.0:     # una línea por segundo en el log
                diag.info("ESTADO " + brain.describe())
                last_diag = time.monotonic()
            if status != RUNNING:
                controls.active = False
                beep()
                log.info("Bot detenido. Revisá el personaje y apretá F9 para seguir.")
            time.sleep(cfg["tick_s"])

    s = brain.stats
    mins = (time.time() - t0) / 60
    log.info(f"Sesión: {mins:.0f} min | mobs: {s['kills']} | potis: {s['potions']} | "
             f"Pick Up: {s['pickups']} (loot cortado por pelea: {s['loot_cortado']}) | "
             f"descansos: {s['rests']} | sin progreso: {s['stuck']} | "
             f"correcciones de sentado: {s['sit_fix']}")


def wait_enter():
    """La ventana negra no se cierra sola: el usuario alcanza a leer qué pasó."""
    try:
        input("\nApretá Enter para cerrar esta ventana...")
    except EOFError:
        pass


if __name__ == "__main__":
    print("=" * 52 + f"\n  Vision Desktop Agent v{VERSION} — juega leyendo la pantalla\n" + "=" * 52)
    try:
        main()
    except KeyboardInterrupt:
        pass
    except Exception:
        import traceback
        err = traceback.format_exc()
        print("\nERROR — mandá este texto o el archivo agent.log:\n\n" + err)
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write("\nERROR\n" + err)
        except Exception:
            pass
    wait_enter()
