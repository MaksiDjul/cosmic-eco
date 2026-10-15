"""
Fixed-point arithmetic helpers (doc 8 "Floating-Point Conservation").

Everything in the simulation is integer arithmetic. The critical primitive is
``allocate_pro_rata``, which implements Pro-Rata Allocation (doc 3.2) using the
largest-remainder method so the parts ALWAYS sum exactly to the whole. That is
what makes "money never appears from or vanishes into nothingness" a provable
property rather than an aspiration.
"""

from .config import BP, CENTS_PER_CREDIT


def allocate_pro_rata(total: int, weights):
    """Distribute ``total`` across ``weights`` proportionally, exactly.

    Returns a list of ints with ``sum(result) == min(total, sum(weights))`` and
    ``result[i] <= weights[i]`` for every i.

    Uses the largest-remainder (Hare) method: every recipient first receives
    its floor share, then the leftover indivisible remainder goes to whoever
    was shortchanged most by the flooring. No fractional units are created and
    none are lost.
    """
    n = len(weights)
    if n == 0 or total <= 0:
        return [0] * n
    s = sum(weights)
    if s <= 0:
        return [0] * n
    if total >= s:
        # Everyone is fully served; nothing to ration.
        return list(weights)

    base = [(total * w) // s for w in weights]
    remainder = total - sum(base)
    if remainder:
        # Rank by the size of the discarded fraction (total*w mod s), then by
        # weight, then by index -- deterministic and reproducible.
        order = sorted(range(n), key=lambda i: (-((total * weights[i]) % s), -weights[i], i))
        for i in order[:remainder]:
            base[i] += 1
    return base


def apply_bp(value: int, bp: int) -> int:
    """Scale ``value`` by a basis-point rate, truncating toward zero."""
    return (value * bp) // BP


def clamp(value, lo, hi):
    return lo if value < lo else (hi if value > hi else value)


def bounded_move(current: int, target: int, damping_bp: int, max_move_bp: int,
                 lo: int, hi: int) -> int:
    """Nudge ``current`` toward ``target`` under damping and a hard step cap.

    This is the shared engine for both commodity price ticks and FX rate ticks
    (doc 3.2 "Price Tick Ticker", doc 3.1 "Dynamic Exchange Rates"). The
    per-tick cap is what prevents price explosions.
    """
    delta = apply_bp(target - current, damping_bp)
    cap = apply_bp(current, max_move_bp)
    if cap < 1:
        cap = 1
    if delta > cap:
        delta = cap
    elif delta < -cap:
        delta = -cap
    if delta == 0 and target != current:
        # Guarantee forward progress so a price can never be frozen by
        # integer truncation alone.
        delta = 1 if target > current else -1
    return clamp(current + delta, lo, hi)


def step_bp(current: int, bp: int, lo: int, hi: int) -> int:
    """Move ``current`` by a bounded percentage step (used by the Explosion
    Safeguard when one side of an order book is completely empty)."""
    delta = apply_bp(current, abs(bp))
    if delta < 1:
        delta = 1
    if bp < 0:
        delta = -delta
    return clamp(current + delta, lo, hi)


# ---------------------------------------------------------------------------
# Display helpers (used only by the presentation layer)
# ---------------------------------------------------------------------------

def money_str(cents: int, symbol: str = "") -> str:
    """Render a cent amount as a human credit figure."""
    neg = cents < 0
    cents = abs(int(cents))
    whole, frac = divmod(cents, CENTS_PER_CREDIT)
    s = f"{whole:,}.{frac:02d}"
    if neg:
        s = "-" + s
    return f"{symbol}{s}" if symbol else s


def money_short(cents: int, symbol: str = "") -> str:
    """Compact money for dense tables: 1.2M, 843k, 12.40."""
    neg = cents < 0
    v = abs(int(cents)) / CENTS_PER_CREDIT
    if v >= 1_000_000_000:
        s = f"{v / 1_000_000_000:.2f}B"
    elif v >= 1_000_000:
        s = f"{v / 1_000_000:.2f}M"
    elif v >= 10_000:
        s = f"{v / 1_000:.1f}k"
    else:
        s = f"{v:,.2f}"
    if neg:
        s = "-" + s
    return f"{symbol}{s}" if symbol else s


def bp_str(bp: int) -> str:
    """Render basis points as a percentage."""
    return f"{bp / 100:.2f}%"


def qty_short(q: int) -> str:
    q = int(q)
    if abs(q) >= 1_000_000:
        return f"{q / 1_000_000:.2f}M"
    if abs(q) >= 10_000:
        return f"{q / 1_000:.1f}k"
    return f"{q:,}"
