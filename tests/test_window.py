"""Pruebas de la búsqueda de la ventana del juego (sin Windows)."""
import os, sys, json, types, tempfile, builtins, io, contextlib

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path[:0] = [os.path.join(HERE, "..")]


class W:
    def __init__(self, title, w=1280, h=720):
        self.title, self.width, self.height = title, w, h


windows = []
sys.modules["pygetwindow"] = types.SimpleNamespace(getAllWindows=lambda: windows)
import agent as g  # noqa: E402


class Clock:
    t = 0.0
    def time(self): return self.t
    def sleep(self, s): self.t += s
    def monotonic(self): return self.t


g.time = Clock()
answers = []
builtins.input = lambda prompt="": answers.pop(0) if answers else ""
tmp = tempfile.mkdtemp()
g.CONFIG_PATH = os.path.join(tmp, "config.json")
results = []


def case(name, wins, cfg_title, inputs, expect_title, expect_saved, known=()):
    windows[:] = wins
    answers[:] = list(inputs)
    json.dump({"ventana_titulo": cfg_title, "otra_cosa": 1}, open(g.CONFIG_PATH, "w"))
    cfg = {"ventana_titulo": cfg_title, "ventana_titulos_conocidos": list(known)}
    with contextlib.redirect_stdout(io.StringIO()):
        w = g.wait_for_game(cfg)
    saved = json.load(open(g.CONFIG_PATH))
    ok = (w.title == expect_title and saved["ventana_titulo"] == expect_saved
          and saved["otra_cosa"] == 1 and not answers)
    print(f"{'OK ' if ok else 'FAIL'} {name}: eligió '{w.title}', config='{saved['ventana_titulo']}'")
    results.append(ok)


console = W(r"C:\Program Files\WindowsApps\PythonSoftwareFoundation\py.exe")
browser = W("Hero Online - guía para principiantes - Google Chrome")

case("título de config coincide", [console, W("Hero Online")], "Hero", [], "Hero Online", "Hero")
case("nombre alternativo conocido → lo detecta solo y guarda la parte fija",
     [console, W("HXO - Personaje")], "Hero", [], "HXO - Personaje", "HXO", known=["HXO"])
case("config guardado y otro personaje: lo encuentra sin preguntar",
     [console, W("HXO - OtroPj")], "HXO", [], "HXO - OtroPj", "HXO")
case("nombre desconocido → lista y elige con número",
     [console, W("Discord"), W("UE4Game (64-bit)")], "Hero", ["3"], "UE4Game (64-bit)", "UE4Game (64-bit)")
case("dos ventanas parecidas (juego + navegador) → pregunta",
     [browser, W("Hero Online"), console], "Hero", ["2"], "Hero Online", "Hero Online")


# juego minimizado: no aparece en la lista; el usuario lo restaura y aprieta Enter
def restore_after_first_prompt(prompt=""):
    windows[:] = [console, W("Hero Online")]
    return ""


windows[:] = [console, W("Hero Online", 160, 28)]
builtins.input = restore_after_first_prompt
json.dump({"ventana_titulo": "Hero"}, open(g.CONFIG_PATH, "w"))
with contextlib.redirect_stdout(io.StringIO()):
    w = g.wait_for_game({"ventana_titulo": "Hero"})
ok = w.title == "Hero Online"
print(f"{'OK ' if ok else 'FAIL'} minimizado → lo restaura → lo encuentra: '{w.title}'")
results.append(ok)

print(f"\n{sum(results)}/{len(results)} pruebas OK")
sys.exit(0 if all(results) else 1)
