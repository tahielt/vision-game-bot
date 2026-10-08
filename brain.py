"""Lógica del bot: busca mob, pelea, se cura, descansa y levanta el loot.

No sabe nada de Windows ni del juego: recibe un objeto `io` que lee las barras
y aprieta teclas. Así se puede probar con un juego simulado (tests/test_sim.py).

io debe tener:
    read_my_hp()  -> (fraccion 0..1, visible)
    read_target() -> (fraccion 0..1, visible)
    press(accion) -> accion in: next_target, next_target_alt, attack, skill, potion, sit,
                                cancel_target, escape, pickup

Loot: el drop aparece un instante después de
que muere el mob y solo se puede levantar el propio y de cerca. Por pantalla no se ven
los ítems, así que después de cada kill se aprieta la acción "Pick Up" del juego (que
camina al ítem más cercano y lo levanta) unas pocas veces. Si lo atacan o la vida baja,
corta el loot: sobrevivir va primero.
"""
import random
from collections import deque

RUNNING, DEAD, ESCAPED = "running", "dead", "escaped"
REST_CHECK_S = 12.0     # cada cuánto verifica que de verdad esté sentado
PROBE_S = 10.0          # duración de cada tramo de la prueba A/B del primer descanso


class Brain:
    def __init__(self, cfg: dict, io, clock, log=print, rng=None):
        self.cfg, self.io, self.clock, self.log = cfg, io, clock, log
        self.rng = rng or random.Random()
        self.reset()

    def reset(self):
        self.hp_hist = deque()          # (t, hp) últimos segundos
        self.dead_since = None
        self.last_potion = -1e9
        self.sitting = False
        self.engaged = False            # peleando con un target vivo
        self.best_target_hp = 1.0
        self.last_progress = 0.0
        self.last_attack = -1e9
        self.last_search = -1e9
        self.searching = False
        self.blind_attack_pending = False
        self.search_fails = 0
        self.pending_pot = None         # [t_press, subio_hp, hp_minimo]
        self.pot_no_effect = 0
        self.out_of_potions = False
        self.loot_left = 0              # cuántas veces más apretar "Pick Up"
        self.loot_debt = 0              # presiones pendientes de un loot cortado
        self.loot_hp_ref = 0.0
        self.next_pick = 0.0
        self.last_kill_t = -1e9
        self.last_skill = -1e9
        self.use_alt_target = False     # alterna F1 / F2 mientras no encuentra objetivo
        self.engage_hp = 1.0            # vida del objetivo cuando empezó la pelea
        self.zero_dmg_streak = 0        # objetivos seguidos a los que no les bajó nada
        self.last_activity = self.clock() # última vez que peleó / descansó / lootó
        self.last_target = (0.0, False)
        self.stand_rate = getattr(self, "stand_rate", None)   # regeneración parado (vida/s)
        self.regen_samples = getattr(self, "regen_samples", [])
        self.last_regen_sample = -1e9
        self.rest_check_t, self.rest_check_hp = 0.0, 0.0
        self.last_sit_change = -1e9
        self.rest_probe = None          # prueba A/B del primer descanso
        self.probe_t, self.probe_hp, self.probe_rate_a = 0.0, 0.0, 0.0
        # las estadísticas se mantienen entre pausas
        self.stats = getattr(self, "stats", None) or {
            "kills": 0, "potions": 0, "stuck": 0, "rests": 0, "pickups": 0, "loot_cortado": 0,
            "skills": 0, "sit_fix": 0}

    def _loot_enabled(self):
        return bool(self.cfg.get("lootear")) and bool(self.cfg["teclas"].get("pickup"))

    def _start_loot(self, now, my):
        if not self._loot_enabled():
            return
        presses = int(self.cfg.get("loot_presiones", 4))
        # lo que quedó sin levantar de un loot cortado se suma (hasta otra tanda)
        self.loot_left = presses + min(self.loot_debt, presses)
        self.loot_debt = 0
        self.loot_hp_ref = my               # HP al momento del kill
        self.next_pick = now + self.cfg.get("loot_espera_s", 0.5)   # el drop tarda en aparecer

    def _has_stand(self):
        return bool(self.cfg["teclas"].get("stand"))

    def _stand_up(self):
        """Pararse. Con macro /stand es seguro (si ya está parado no hace nada);
        sin ella, F6 alterna sentarse/pararse."""
        self.io.press("stand" if self._has_stand() else "sit")
        self.last_sit_change = self.clock()

    def _fix_sitting(self, why):
        """El personaje quedó sentado sin que el bot lo sepa (un F6 que el juego no tomó)."""
        if not (self._has_stand() or self.cfg["teclas"].get("sit")):
            return
        self.log(f"Parece que quedó sentado ({why}) → se para")
        self._stand_up()
        self.stats["sit_fix"] += 1

    def _escape(self, why):
        self.log(f"{why} → Scroll of Escape. Bot detenido.")
        self.io.press("escape")
        self.io.press("escape")             # dos veces, por si el juego no tomó la primera
        return ESCAPED

    def describe(self):
        """Una línea para el log de diagnóstico."""
        my = self.hp_hist[-1][1] if self.hp_hist else 0.0
        t_hp, t_vis = self.last_target
        if self.sitting:
            state = "descansando"
        elif self.engaged:
            state = f"peleando ({self.clock() - self.last_progress:.0f}s sin bajarle vida)"
        elif self.loot_left:
            state = f"levantando loot (faltan {self.loot_left})"
        else:
            state = "buscando objetivo"
        tgt = f"{t_hp:.0%}" if t_vis else "no se ve"
        s = self.stats
        return (f"vida {my:.0%} | objetivo {tgt} | {state} | mobs {s['kills']} | "
                f"F4 {s['pickups']} | sin progreso {s['stuck']}")

    # ---------- helpers ----------
    def _jit(self, base):
        return base * self.rng.uniform(0.85, 1.15)

    def _taking_damage(self, now, window=2.0, since=None):
        """¿Bajó el HP más de 2% en los últimos `window` segundos (y después de `since`)?"""
        start = now - window if since is None else max(now - window, since)
        old = [hp for t, hp in self.hp_hist if t >= start]
        return bool(old) and max(old) - self.hp_hist[-1][1] > 0.02

    def _measure_stand_regen(self, now, my):
        """Regeneración parado, medida en ventanas de 6 s sin pelea, sin daño y sin poti."""
        if self.sitting or self.engaged or my >= 0.98 or now - self.last_potion < 12.0:
            return
        old = [(t, hp) for t, hp in self.hp_hist if now - t >= 6.0]
        if not old:
            return
        t0, hp0 = old[-1]
        if t0 < self.last_sit_change + 1.0:     # la ventana incluye tiempo sentado: no sirve
            return
        window = [hp for t, hp in self.hp_hist if t >= t0]
        if min(window) < hp0 - 0.005:          # hubo daño en la ventana: no sirve
            return
        if now - self.last_regen_sample < 6.0:   # ventanas que no se pisen
            return
        self.last_regen_sample = now
        rate = max(0.0, (my - hp0) / (now - t0))
        self.regen_samples.append(rate)
        if len(self.regen_samples) > 9:
            self.regen_samples.pop(0)
        ordered = sorted(self.regen_samples)
        self.stand_rate = ordered[len(ordered) // 2]   # mediana: ignora mediciones raras

    def _rest_probe_step(self, now, my):
        """Prueba A/B para saber si quedó sentado y cuánto regenera parado."""
        if now - self.probe_t < PROBE_S and my < self.cfg["descansar_hasta"]:
            return RUNNING
        rate = (my - self.probe_hp) / max(now - self.probe_t, 1e-6)
        if self.rest_probe == "A":
            self.probe_rate_a = rate
            self.io.press("sit")                       # cambia de estado para comparar
            self.last_sit_change = now
            self.rest_probe = "B"
            self.probe_t, self.probe_hp = now, my
            return RUNNING
        ra, rb = self.probe_rate_a, rate
        self.rest_probe = None
        self.rest_check_t, self.rest_check_hp = now, my
        if ra <= 0 or rb <= 0:                         # algún tramo no regeneró (¿golpe?): no concluye
            return None
        if rb > ra * 1.2:                              # B más rápido: ahora está sentado
            self.stand_rate = ra
            self.regen_samples = [ra]
            self.log(f"Regeneración medida: parado {ra*100:.2f}%/s, sentado {rb*100:.2f}%/s")
        elif ra > rb * 1.2:                            # A era sentado: volver a sentarse
            self.io.press("sit")
            self.last_sit_change = now
            self.stand_rate = rb
            self.regen_samples = [rb]
            self.log(f"Regeneración medida: parado {rb*100:.2f}%/s, sentado {ra*100:.2f}%/s")
        return None

    def _hp_rising(self, now):
        """¿La vida sube rápido sin poti? (sentado se regenera mucho más que parado)"""
        if now - self.last_potion < 12.0 or len(self.hp_hist) < 2:
            return False
        lows = [hp for t, hp in self.hp_hist if now - t <= 3.0]
        return bool(lows) and self.hp_hist[-1][1] - min(lows) >= 0.015

    def _check_potion_effect(self, now):
        """¿La última poti subió el HP? 3 potis seguidas sin efecto = no quedan potis."""
        if self.pending_pot is None:
            return
        # sirve para potis instantáneas y de curación gradual: ¿subió respecto del mínimo desde que la tomó?
        my = self.hp_hist[-1][1]
        self.pending_pot[2] = min(self.pending_pot[2], my)
        if my > self.pending_pot[2] + 0.01:
            self.pending_pot[1] = True
        if now - self.pending_pot[0] < 4.0:
            return
        worked = self.pending_pot[1]
        self.pending_pot = None
        if worked:
            self.pot_no_effect = 0
            if self.out_of_potions:
                self.out_of_potions = False
                self.log("Las potis vuelven a funcionar → modo normal")
        else:
            self.pot_no_effect += 1
            if self.pot_no_effect >= 3 and not self.out_of_potions:
                self.out_of_potions = True
                self.log("SIN POTIS (3 potis sin efecto) → modo conservador: descansa antes de cada mob")

    # ---------- un paso del bot ----------
    def tick(self) -> str:
        c, now = self.cfg, self.clock()
        my, _ = self.io.read_my_hp()

        self.hp_hist.append((now, my))
        while self.hp_hist and now - self.hp_hist[0][0] > 15.0:
            self.hp_hist.popleft()

        # --- muerte: HP 0 sostenido 3 s ---
        if my <= 0.0:
            self.dead_since = self.dead_since or now
            if now - self.dead_since >= 3.0:
                self.log("MURIÓ (o la barra de HP no se ve). Bot detenido.")
                return DEAD
            return RUNNING
        self.dead_since = None

        self._check_potion_effect(now)
        cooldown = max(20.0, c["pocion_cooldown_s"]) if self.out_of_potions else c["pocion_cooldown_s"]  # sin potis: reintenta c/20 s
        potion_ready = now - self.last_potion >= cooldown
        rest_below = max(c["descansar_debajo_de"], 0.90) if self.out_of_potions else c["descansar_debajo_de"]

        has_escape = bool(c["teclas"].get("escape"))

        # --- sin potis y con escape configurado: volver al pueblo antes de arriesgarse ---
        if self.out_of_potions and has_escape and c.get("sin_potis_escapar", True):
            return self._escape("Sin potis")

        # --- emergencia: HP crítico y la poti en cooldown → escape (si hay tecla) ---
        if my < c["emergencia_debajo_de"] and not potion_ready and has_escape:
            return self._escape(f"HP crítico ({my:.0%})")

        # --- poción (opcional: solo mientras pelea) ---
        in_fight_ok = self.engaged or not c.get("pocion_solo_peleando", False)
        if my < c["pocion_debajo_de"] and potion_ready and in_fight_ok:
            self.io.press("potion")
            self.last_potion = now
            self.stats["potions"] += 1
            self.pending_pot = [now, False, my]   # [t_press, subio_hp, hp_minimo]

        self._measure_stand_regen(now, my)

        # --- descansando ---
        if self.sitting:
            # Primer descanso de la sesión: todavía no sabe cuánto regenera parado.
            # Prueba A/B: mide 10 s, aprieta F6, mide 10 s; el tramo más rápido era "sentado".
            if self.rest_probe and not self._taking_damage(now):
                done = self._rest_probe_step(now, my)
                if done is not None:
                    return done
            # ¿De verdad se sentó? Sentado regenera claramente más rápido que parado.
            # Si no, el F6 se perdió: lo aprieta de nuevo (si no, esperaría minutos parado).
            elif now - self.rest_check_t >= REST_CHECK_S and not self._taking_damage(now):
                rate = (my - self.rest_check_hp) / (now - self.rest_check_t)
                ref = self.stand_rate * 1.25 if self.stand_rate is not None else 0.0
                slow = rate <= max(ref, 0.001)     # y nunca menos de 0,1 %/s
                if slow and my < c["descansar_hasta"] - 0.02 and now - self.last_potion >= 12.0:
                    self.log("No parece estar sentado (no regenera más rápido) → F6 de nuevo")
                    self.io.press("sit")
                    self.last_sit_change = now
                    self.stats["sit_fix"] += 1
                self.rest_check_t, self.rest_check_hp = now, my
            if my >= c["descansar_hasta"] or self._taking_damage(now):
                if self._taking_damage(now):
                    self.log("Lo atacan mientras descansa → se para")
                self._stand_up()
                self.sitting = False
                self.rest_probe = None          # una prueba A/B a medias no sirve más
            self.last_activity = now
            return RUNNING

        t_hp, t_vis = self.io.read_target()
        self.last_target = (t_hp, t_vis)
        alive = t_vis and t_hp > c.get("objetivo_muerto_debajo_de", 0.01)

        # --- peleando ---
        if alive:
            self.search_fails = 0
            self.searching = False
            self.last_activity = now
            if not self.engaged:
                self.engaged = True
                self.engage_hp = t_hp
                self.best_target_hp = t_hp
                self.last_progress = now
                self.io.press("attack")
                self.last_attack = now
            if t_hp < self.best_target_hp - 0.005:
                self.best_target_hp = t_hp
                self.last_progress = now
                self.zero_dmg_streak = 0        # le está pegando: no está sentado
            # skill (F5) mientras el objetivo tenga más de X % de vida
            if (c["teclas"].get("skill") and t_hp > c.get("skill_si_objetivo_arriba_de", 0.5)
                    and now - self.last_skill >= self._jit(c.get("skill_cada_s", 2.0))):
                self.io.press("skill")
                self.last_skill = now
                self.stats["skills"] += 1
            if now - self.last_attack >= self._jit(c["reatacar_cada_s"]):
                self.io.press("attack")
                self.last_attack = now
            # VIGILANCIA: el objetivo no cambia → no quedarse parado. Puede ser un mob trabado o
            # un mob muerto cuya barra se sigue viendo: cancela, lootea por las dudas y busca otro.
            if now - self.last_progress >= c.get("sin_progreso_s", c.get("mob_trabado_s", 15)):
                no_damage = self.best_target_hp >= self.engage_hp - 0.005
                self.log(f"Objetivo sin cambios ({t_hp:.0%}) → cancela, F4 por las dudas y busca otro")
                if hasattr(self.io, "snapshot"):
                    self.io.snapshot("sin_progreso")
                self.io.press("cancel_target")
                self.engaged = False
                self.stats["stuck"] += 1
                if self._has_stand() and no_damage:
                    self._stand_up()            # por las dudas: con /stand no hay riesgo
                self.zero_dmg_streak = self.zero_dmg_streak + 1 if no_damage else 0
                # 2 objetivos seguidos sin poder bajarles nada + una pista de que está sentado
                # (sentado regenera rápido; o le pegan y no responde; o vida llena = no hay cómo saberlo).
                if self.zero_dmg_streak >= 2 and (self._hp_rising(now) or self._taking_damage(now) or my >= 0.97):
                    self._fix_sitting("2 objetivos seguidos sin poder atacarlos")
                    self.zero_dmg_streak = 0
                self._start_loot(now, my)
            return RUNNING

        # --- no hay target vivo ---
        if self.engaged:
            self.engaged = False
            self.last_kill_t = now          # el daño de antes de esto era del mob que ya murió
            self.stats["kills"] += 1
            if self.stats["kills"] % 25 == 0:
                self.log(f"Mobs matados: {self.stats['kills']} | Pick Up apretado: {self.stats['pickups']}")
            self._start_loot(now, my)

        # --- levantar loot ---
        if self.loot_left > 0:
            self.loot_hp_ref = max(self.loot_hp_ref, my)
            # Solo daño NUEVO corta el loot: el último golpe del mob que acaba de morir no cuenta.
            if my < self.loot_hp_ref - 0.02:
                self.loot_debt += self.loot_left    # se retoma después del próximo kill
                self.loot_left = 0
                self.stats["loot_cortado"] += 1
            elif now >= self.next_pick:
                self.last_activity = now
                self.io.press("pickup")
                self.stats["pickups"] += 1
                self.loot_left -= 1
                self.next_pick = now + self._jit(c.get("loot_intervalo_s", 0.7))
                return RUNNING
            else:
                return RUNNING

        if my < rest_below and not self._taking_damage(now, window=3.0, since=self.last_kill_t):
            self.io.press("sit")
            self.sitting = True
            self.last_sit_change = now
            self.rest_check_t, self.rest_check_hp = now, my
            self.rest_probe = None
            if self.stand_rate is None and c["descansar_hasta"] - my > 0.15:
                self.rest_probe = "A"
                self.probe_t, self.probe_hp = now, my
            self.stats["rests"] += 1
            return RUNNING

        # Ataque "a ciegas" después de buscar: si el target tiene tan poca vida que la barra
        # se ve vacía, "Next Target" lo vuelve a elegir (es el más cercano) y nunca se le pegaría.
        # Si no hay nada seleccionado, el juego ignora la tecla.
        if self.blind_attack_pending and now - self.last_search >= 0.3:
            self.io.press("attack")
            self.blind_attack_pending = False
            return RUNNING

        # VIGILANCIA mientras busca: mucho rato sin poder pelear. Si además le pegan (o la vida
        # sube como sentado), casi seguro quedó sentado por un F6 perdido → se para.
        idle = now - max(self.last_activity, self.last_kill_t)
        if self.searching and idle >= 2 * c.get("sin_progreso_s", 6):
            self.log(f"{idle:.0f} s buscando sin poder pelear → cancela objetivo y revisa si quedó sentado")
            if hasattr(self.io, "snapshot"):
                self.io.snapshot("buscando_sin_pelear")
            self.io.press("cancel_target")
            if self._has_stand() or self._taking_damage(now) or self._hp_rising(now):
                self._fix_sitting("le pegan o regenera como sentado mientras busca")
            self.last_activity = now
            return RUNNING

        if now - self.last_search >= self._jit(c["buscar_cada_s"]):
            if self.searching:
                self.search_fails += 1
                if self.search_fails % 20 == 0:
                    self.log("No encuentra mobs a la vista; ¿está en la zona de farm?")
                # F1 no encontró nada → prueba F2 ("o en su defecto F2"), y así alternando
                self.use_alt_target = bool(c["teclas"].get("next_target_alt")) and not self.use_alt_target
            else:
                self.use_alt_target = False
            self.io.press("next_target_alt" if self.use_alt_target else "next_target")
            self.last_search = now
            self.searching = True
            self.blind_attack_pending = True
        return RUNNING
