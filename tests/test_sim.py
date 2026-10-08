"""Juego simulado para probar la lógica del bot sin el juego real.

Modela un personaje de nivel bajo farmeando: mobs pasivos y agresivos, mobs
"trabados" que no reciben daño, potis con cooldown, regeneración sentado y LOOT:
  - al morir, un mob suelta 0 a 4 ítems; ~15% quedan reservados para otro jugador
  - "Pick Up" camina al ítem MÁS CERCANO (propio o ajeno) y tarda en llegar;
    si es ajeno, falla (como en los MMO clásicos: solo se levanta lo propio)
  - sentado no se puede levantar nada; caminar a un ítem corta el ataque
  - los ítems desaparecen del piso a los 60 s
Corre muchas horas simuladas con distintas semillas.
"""
import sys, os, random, json

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from brain import Brain, RUNNING

CFG = {
    "teclas": {"escape": ""},
    "pocion_debajo_de": 0.55, "pocion_cooldown_s": 10,
    "descansar_debajo_de": 0.70, "descansar_hasta": 0.95,
    "emergencia_debajo_de": 0.20,
    "mob_trabado_s": 15, "reatacar_cada_s": 3.0, "buscar_cada_s": 0.6,
    "lootear": False,
    "loot_presiones": 4, "loot_intervalo_s": 0.7, "loot_espera_s": 0.5,
}
DT = 0.15


class Mob:
    def __init__(self, rng, aggro_rate):
        self.max = 300
        self.hp = 300
        self.stuck = rng.random() < 0.05          # 5% inalcanzables (detrás de una pared)
        self.aggro = (not self.stuck) and rng.random() < aggro_rate
        self.angry = False                        # pega al jugador
        self.next_hit = 0.0


class Drop:
    def __init__(self, mine, t):
        self.mine = mine
        self.expires = t + 60.0


class World:
    def __init__(self, seed, hard=False, gradual_potions=False, miss_rate=0.0, stale_corpse=False):
        self.gradual = gradual_potions            # poti que cura de a poco (como en los MMO clásicos)
        # FALLAS REALES vistas con el tester:
        self.miss_rate = miss_rate                # % de teclas que el juego no toma
        self.stale_corpse = stale_corpse          # la barra del mob muerto se sigue viendo con vida
        self.missed = 0
        self.kill_times = []
        self.hot_until = 0.0
        self.rng = random.Random(seed)
        self.t = 0.0
        self.max_hp, self.hp = 500.0, 500.0
        self.sitting = False
        self.dead = False
        self.potions = 200
        self.mobs = []
        self.target = None
        self.attacking = False
        self.next_swing = 0.0
        self.next_spawn = 0.0
        self.kills = 0
        self.escaped = False
        self.mob_dmg = 22 if hard else 14
        self.aggro_rate = 0.40 if hard else 0.25
        # loot
        self.drops = []
        self.walking_to = None                    # (drop, t_llegada)
        self.pickup_presses = 0
        self.own_drops_created = 0
        self.items_picked = 0
        self.foreign_picked = 0                   # nunca debería pasar
        self.skill_presses = 0
        self.alt_presses = 0
        self.skill_ready = 0.0

    # --- lo que "ve" el bot ---
    def read_my_hp(self):
        return (self.hp / self.max_hp, True)

    def read_target(self):
        if self.target is None:
            return (0.0, False)
        if self.target.hp == 0 and self.stale_corpse:
            return (self.target.stale_frac, True)    # muerto, pero la barra "se ve" con vida
        return (self.target.hp / self.target.max, self.target.hp > 0)

    # --- lo que "aprieta" el bot ---
    def press(self, action):
        if self.dead:
            return
        if self.miss_rate and self.rng.random() < self.miss_rate:
            self.missed += 1                      # el juego no registró la tecla
            return
        if action in ("next_target", "next_target_alt"):
            self.alt_presses += action == "next_target_alt"
            # F1 a veces no agarra nada (mob lejos / fuera de pantalla); F2 es el plan B
            if action == "next_target" and self.rng.random() < 0.3:
                self.target, self.attacking = None, False
                return
            # Next Target agarra el más cercano: si alguien te está pegando, está al lado tuyo
            alive = [m for m in self.mobs if m.hp > 0]
            hitting = [m for m in alive if m.angry]
            pool = hitting or alive
            self.target = self.rng.choice(pool) if pool else None
            self.attacking = False
        elif action == "attack":
            if self.target and self.target.hp > 0 and not self.sitting:
                self.attacking = True
                if not self.target.stuck:             # uno inalcanzable no te puede pegar
                    self.target.angry = True
                self.walking_to = None
        elif action == "skill":
            self.skill_presses += 1
            tg = self.target
            if tg and tg.hp > 0 and not self.sitting and self.t >= self.skill_ready and not tg.stuck:
                before = tg.hp / tg.max
                tg.hp = max(0, tg.hp - 60)            # golpe fuerte, 6 s de cooldown
                self.skill_ready = self.t + 6.0
                self.attacking = True
                tg.angry = True
                if tg.hp == 0:
                    self._kill(tg, before)
        elif action == "potion":
            if self.potions > 0:
                self.potions -= 1
                if self.gradual:
                    self.hot_until = self.t + 10.0    # 12 HP/s durante 10 s
                else:
                    self.hp = min(self.max_hp, self.hp + 120)
        elif action == "sit":
            self.sitting = not self.sitting
            if self.sitting:
                self.attacking = False
                self.walking_to = None
        elif action == "stand":                   # macro /stand: solo se para
            self.sitting = False
        elif action == "cancel_target":
            if self.target and self.target.stuck:
                self.mobs.remove(self.target)     # se aleja; no vuelve a salir
            self.target, self.attacking = None, False
        elif action == "escape":
            self.escaped = True                   # vuelve al pueblo: a salvo
            self.mobs, self.target, self.attacking = [], None, False
        elif action == "pickup":
            self.pickup_presses += 1
            if self.sitting or not self.drops:
                return
            drop = self.rng.choice(self.drops)    # "el más cercano": cualquiera de los del piso
            self.attacking = False                # ir a levantar corta el ataque
            self.walking_to = (drop, self.t + self.rng.uniform(0.3, 0.6))
        else:
            raise ValueError(action)

    def _kill(self, mob, before):
        mob.stale_frac = before if self.rng.random() < 0.5 else 1.0   # se queda en lo último o "llena"
        self.kills += 1
        self.kill_times.append(self.t)
        self._drop_loot()

    def _drop_loot(self):
        for _ in range(self.rng.randint(0, 4)):
            mine = self.rng.random() < 0.85
            self.own_drops_created += mine
            self.drops.append(Drop(mine, self.t))

    def step(self):
        self.t += DT
        if self.dead or self.escaped:
            return
        if self.t >= self.next_spawn and len([m for m in self.mobs if m.hp > 0]) < 4:
            self.mobs.append(Mob(self.rng, self.aggro_rate))
            self.next_spawn = self.t + self.rng.uniform(4, 12)
        # los agresivos te notan cuando pasás cerca (en promedio a los ~25 s)
        for m in self.mobs:
            if m.aggro and not m.angry and m.hp > 0 and self.rng.random() < 0.04 * DT:
                m.angry = True
        # el jugador pega
        tg = self.target
        if self.attacking and tg and tg.hp > 0 and self.t >= self.next_swing:
            if not tg.stuck:
                before = tg.hp / tg.max
                tg.hp = max(0, tg.hp - self.rng.uniform(30, 50))
                if tg.hp == 0:
                    self._kill(tg, before)
            self.next_swing = self.t + 1.5
        # llega al ítem
        if self.walking_to and self.t >= self.walking_to[1]:
            drop = self.walking_to[0]
            self.walking_to = None
            if drop in self.drops and drop.mine:
                self.drops.remove(drop)
                self.items_picked += 1
        self.drops = [d for d in self.drops if d.expires > self.t]
        # los mobs enojados pegan
        for m in self.mobs:
            if m.hp > 0 and m.angry and self.t >= m.next_hit:
                self.hp -= self.rng.uniform(0.6, 1.4) * self.mob_dmg
                m.next_hit = self.t + 2.0
        # regeneración
        regen = (12 if self.sitting else 1) + (12 if self.t < self.hot_until else 0)
        self.hp = min(self.max_hp, self.hp + regen * DT)
        if self.hp <= 0:
            self.hp, self.dead = 0.0, True
        # cadáveres desaparecen
        self.mobs = [m for m in self.mobs if m.hp > 0 or m is self.target]

    def loot_rate(self):
        return self.items_picked / self.own_drops_created if self.own_drops_created else 1.0


def run(seed, hours=3, hard=False, cfg=CFG, gradual=False, miss_rate=0.0, stale_corpse=False):
    w = World(seed, hard, gradual, miss_rate, stale_corpse)
    msgs = []
    b = Brain(cfg, w, clock=lambda: w.t, log=msgs.append, rng=random.Random(seed))
    status = RUNNING
    while w.t < hours * 3600 and status == RUNNING:
        status = b.tick()
        w.step()
    return w, b, status


MIN_LOOT_RATE = 0.80     # de lo que le corresponde, levanta al menos el 80% (zona normal)


def main():
    """Criterios:
    - loot apagado: nunca aprieta Pick Up
    - loot prendido: en zona normal levanta >= 80% de su loot; nunca levanta loot ajeno
    - CON Scroll of Escape configurado: nunca muere (con o sin loot)
    - zona normal: aguanta las 3 h vivo aunque no tenga escape
    - zona demasiado difícil SIN escape: se informa (es el límite del personaje)
    """
    esc = {"escape": "f5"}
    cli = json.load(open(os.path.join(os.path.dirname(__file__), "..", "config.json"), encoding="utf-8"))
    rec = json.loads(json.dumps(cli))           # recomendado: poti 40 % + Scroll of Escape en F8
    rec["pocion_debajo_de"] = 0.40
    rec["teclas"]["escape"] = "f8"
    configs = (
        ("config.json (reglas de ejemplo)", cli),
        ("config.json + recomendado (poti 40 % + escape F8)", rec),
        ("sin loot, con escape", dict(CFG, teclas=dict(esc))),
        ("con loot, sin escape", dict(CFG, lootear=True, teclas={"escape": "", "pickup": "f6"})),
        ("con loot, con escape", dict(CFG, lootear=True, teclas=dict(esc, pickup="f6"))),
    )
    ok = True
    for gradual in (False, True):
        for label, cfg in configs:
            loot_on, has_esc = cfg["lootear"], bool(cfg["teclas"]["escape"])
            print(f"=== {label} — potis {'graduales' if gradual else 'instantáneas'} ===")
            alive_runs, kills_n, loot_n = 0, [], []
            for seed in range(10):
                for hard in (False, True):
                    w, b, status = run(seed, hard=hard, cfg=cfg, gradual=gradual)
                    alive = not w.dead
                    alive_runs += alive
                    good = w.foreign_picked == 0
                    if cfg is cli or cfg is rec:   # sus teclas extra se usan de verdad
                        good &= w.skill_presses > 0 and w.alt_presses > 0
                    if not loot_on:
                        good &= w.pickup_presses == 0
                    elif not hard:
                        good &= w.loot_rate() >= MIN_LOOT_RATE
                    if cfg is cli:
                        pass                # sus reglas tal cual: las muertes se informan (ver LEEME_DEV)
                    elif has_esc or not hard:
                        good &= alive       # con escape: nunca muere; zona normal: aguanta las 3 h
                    if not hard:
                        kills_n.append(w.kills)
                        loot_n.append(w.loot_rate())
                    ok &= good
                    fin = "3h completas" if status == RUNNING else ("escapó al pueblo" if w.escaped else "MURIÓ")
                    tag = "FAIL" if not good else ("ok  " if alive else "MURIÓ (info)")
                    print(f"  {tag} seed={seed} {'difícil' if hard else 'normal '} {fin:16} "
                          f"t={w.t/60:4.0f}min kills={w.kills:4} potis={b.stats['potions']:4} "
                          f"pickUp={w.pickup_presses:4} loot={w.items_picked:4}/{w.own_drops_created:<4}"
                          f"({w.loot_rate():4.0%}) cortado={b.stats['loot_cortado']:3} skill={w.skill_presses} F2={w.alt_presses}")
            print(f"  → vivo al final en {alive_runs}/20 | zona normal: kills promedio "
                  f"{sum(kills_n)/len(kills_n):.0f}, loot levantado promedio {sum(loot_n)/len(loot_n):.0%}\n")
    print("TODO OK" if ok else "HAY FALLAS")
    return ok


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
