"""Robustez ante las fallas que vio el tester en el juego real.

  A) teclas que el juego no toma: 5 % (lo que puede quedar con las teclas sostenidas)
     y 20 % (prueba de estrés)
  B) la barra del mob muerto se sigue viendo con vida (o llena)
  C) A 5 % + B juntas

Configs: config.json, config.json + macro /stand (F11) y la recomendada
(poti 40 % + Scroll of Escape F8 + /stand F11).
Criterios: no se traba (peor lapso sin matar <= 3 min; <= 10 min en el estrés),
mata al menos la mitad que sin fallas (salvo estrés), levanta loot (>= 60 %),
y la recomendada nunca muere.
"""
import sys, os, json

sys.path.insert(0, os.path.dirname(__file__))
from test_sim import run

HERE = os.path.dirname(__file__)
cli = json.load(open(os.path.join(HERE, "..", "config.json"), encoding="utf-8"))
cli_stand = json.loads(json.dumps(cli))
cli_stand["teclas"]["stand"] = "f11"
rec = json.loads(json.dumps(cli_stand))
rec["pocion_debajo_de"] = 0.40
rec["teclas"]["escape"] = "f8"

#        nombre                                  teclas perdidas  barra muerto  estrés
MODES = [("sin fallas",                          0.00, False, False),
         ("A) 5% teclas perdidas",               0.05, False, False),
         ("B) barra del muerto se ve con vida",  0.00, True,  False),
         ("C) 5% perdidas + barra del muerto",   0.05, True,  False),
         ("ESTRÉS: 20% teclas perdidas",         0.20, False, True)]
SEEDS, HOURS = range(8), 2


def max_gap(w):
    t = [0.0] + w.kill_times + [w.t]
    return max(b - a for a, b in zip(t, t[1:]))


def main():
    ok = True
    for cname, cfg in (("config.json", cli), ("config.json + /stand en F11", cli_stand),
                       ("recomendada (poti 40% + escape F8 + /stand F11)", rec)):
        base = None
        print(f"=== {cname} ===")
        for mname, miss, stale, stress in MODES:
            kills, loot, gaps, dead, esc, fixes = [], [], [], 0, 0, 0
            for seed in SEEDS:
                w, b, st = run(seed, hours=HOURS, cfg=cfg, gradual=True, miss_rate=miss, stale_corpse=stale)
                kills.append(w.kills); loot.append(w.loot_rate()); dead += w.dead; esc += w.escaped
                fixes += b.stats["sit_fix"]
                if not w.dead and not w.escaped:
                    gaps.append(max_gap(w))
            k = sum(kills) / len(kills)
            base = base or k
            lr = sum(loot) / len(loot)
            worst = max(gaps) if gaps else 0
            good = worst <= (600 if stress else 180) and lr >= 0.6
            if not stress:
                good &= k >= 0.5 * base
            if cfg is rec:
                good &= dead == 0
            ok &= good
            print(f"  {'OK ' if good else 'FAIL'} {mname:36} mobs/{HOURS}h {k:5.0f} ({k/base:4.0%}) | loot {lr:4.0%} | "
                  f"peor lapso sin matar {worst:4.0f}s | murió {dead}/{len(SEEDS)} | escapó {esc} | "
                  f"correcciones de sentado {fixes}")
    print("\nTODO OK" if ok else "\nHAY FALLAS")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
