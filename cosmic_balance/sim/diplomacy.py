"""
DIPLOMACY & STATE RELATIONS (doc 6).

*   State Relations -- Peace / Alliance / War, managed by an automated AI
    evaluation matrix.
*   War Exhaustion -- a progressive index driven by fleet casualties, economic
    stagnation and sector losses, which eventually forces factions to the
    negotiating table.
"""

from . import config
from .faction import PEACE, WAR, ALLIANCE
from .fixed import clamp


def evaluate(world):
    """Run the automated relation matrix. Called every DIPLO_EVAL_INTERVAL."""
    keys = [k for k, f in world.factions.items() if not world.is_pirate(k)]

    # --- 1. opinion drift and structural friction -------------------------
    for a in keys:
        fa = world.factions[a]
        for b in keys:
            if a == b:
                continue
            op = fa.opinion[b]
            # decay toward neutral
            if op > 0:
                op -= min(op, config.DIPLO_OPINION_DRIFT)
            elif op < 0:
                op += min(-op, config.DIPLO_OPINION_DRIFT)
            fa.opinion[b] = op

            # Shared borders breed friction.
            if world.shares_border(a, b):
                fa.adjust_opinion(b, -config.DIPLO_BORDER_FRICTION)

            # Being at war is itself corrosive.
            if fa.stance(b) == WAR:
                fa.adjust_opinion(b, -config.DIPLO_BORDER_FRICTION // 2)

        if fa.war_cooldown:
            for k in list(fa.war_cooldown):
                if fa.war_cooldown[k] > 0:
                    fa.war_cooldown[k] -= config.DIPLO_EVAL_INTERVAL

    # --- 2. economic stagnation feeds exhaustion --------------------------
    for a in keys:
        fa = world.factions[a]
        at_war = any(fa.stance(b) == WAR for b in keys if b != a)
        if at_war:
            fa.add_exhaustion(config.EXHAUSTION_PER_TICK_AT_WAR *
                              config.DIPLO_EVAL_INTERVAL // 4)
        else:
            fa.decay_exhaustion()

    # --- 3. peace offers --------------------------------------------------
    for a in keys:
        fa = world.factions[a]
        for b in keys:
            if a >= b:
                continue
            fb = world.factions[b]
            if fa.stance(b) != WAR:
                continue
            # Either side being exhausted enough brings both to the table.
            if fa.exhaustion >= config.EXHAUSTION_PEACE_THRESHOLD or \
                    fb.exhaustion >= config.EXHAUSTION_PEACE_THRESHOLD:
                make_peace(world, a, b)

    # --- 4. war declarations ---------------------------------------------
    for a in keys:
        fa = world.factions[a]
        if fa.is_player:
            continue          # the human declares their own wars
        if fa.exhaustion >= config.EXHAUSTION_PEACE_THRESHOLD // 2:
            continue          # too tired to start another fight
        for b in keys:
            if a == b:
                continue
            if fa.stance(b) != PEACE:
                continue
            if fa.war_cooldown.get(b, 0) > 0:
                continue
            if fa.opinion[b] > config.DIPLO_WAR_OPINION:
                continue
            if not world.shares_border(a, b):
                continue
            mine = world.faction_strength(a)
            theirs = world.faction_strength(b)
            if theirs <= 0:
                ratio = config.BP * 10
            else:
                ratio = (mine * config.BP) // theirs
            if ratio < config.DIPLO_WAR_STRENGTH_RATIO_BP:
                continue
            declare_war(world, a, b)
            break

    # --- 5. alliances -----------------------------------------------------
    for a in keys:
        fa = world.factions[a]
        for b in keys:
            if a >= b:
                continue
            fb = world.factions[b]
            if fa.stance(b) != PEACE:
                continue
            if fa.opinion[b] >= config.DIPLO_ALLY_OPINION and \
                    fb.opinion[a] >= config.DIPLO_ALLY_OPINION:
                fa.set_stance(b, ALLIANCE)
                fb.set_stance(a, ALLIANCE)
                world.log(f"{fa.name} and {fb.name} signed an alliance")


def declare_war(world, a, b):
    fa, fb = world.factions[a], world.factions[b]
    if fa.stance(b) == WAR:
        return False
    fa.set_stance(b, WAR)
    fb.set_stance(a, WAR)
    fa.adjust_opinion(b, -2_000)
    fb.adjust_opinion(a, -4_000)
    fa.war_cooldown[b] = config.DIPLO_WAR_DECLARE_COOLDOWN
    fb.war_cooldown[a] = config.DIPLO_WAR_DECLARE_COOLDOWN
    world.log(f"{fa.name} declared WAR on {fb.name}")
    return True


def make_peace(world, a, b):
    fa, fb = world.factions[a], world.factions[b]
    if fa.stance(b) != WAR:
        return False
    fa.set_stance(b, PEACE)
    fb.set_stance(a, PEACE)
    # Peace relieves exhaustion on both sides and resets the grudge a little.
    fa.exhaustion = clamp(fa.exhaustion // 2, 0, config.EXHAUSTION_MAX)
    fb.exhaustion = clamp(fb.exhaustion // 2, 0, config.EXHAUSTION_MAX)
    fa.adjust_opinion(b, 1_500)
    fb.adjust_opinion(a, 1_500)
    fa.war_cooldown[b] = config.DIPLO_WAR_DECLARE_COOLDOWN
    fb.war_cooldown[a] = config.DIPLO_WAR_DECLARE_COOLDOWN
    world.log(f"{fa.name} and {fb.name} agreed a white peace")
    return True
