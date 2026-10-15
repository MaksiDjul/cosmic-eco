"""
THE GALACTIC BOARD (doc 2) -- a hexagonal sector grid.

Axial coordinates (q, r) with the standard six-neighbour adjacency. Pathfinding
for merchant routing is deliberately radius-limited (doc 3.4 "Algorithmic
Pathfinding & Distance Sinks") so route evaluation stays cheap on large maps.
"""

from collections import deque

# Axial neighbour offsets, clockwise from "east".
HEX_DIRECTIONS = [(1, 0), (1, -1), (0, -1), (-1, 0), (-1, 1), (0, 1)]


def hex_neighbors(q, r):
    for dq, dr in HEX_DIRECTIONS:
        yield q + dq, r + dr


def hex_distance(a, b):
    """Axial hex distance between two (q, r) coordinates."""
    aq, ar = a
    bq, br = b
    dq = aq - bq
    dr = ar - br
    if (dq < 0) == (dr < 0):
        return abs(dq + dr)
    return max(abs(dq), abs(dr))


def hex_ring(radius):
    """All axial coordinates at exactly ``radius`` from the origin."""
    if radius == 0:
        yield (0, 0)
        return
    q, r = -radius, radius     # start on the south-west corner
    for i in range(6):
        dq, dr = HEX_DIRECTIONS[i]
        for _ in range(radius):
            yield (q, r)
            q += dq
            r += dr


def hex_spiral(radius):
    """All coordinates within ``radius`` of the origin, centre outward."""
    for k in range(radius + 1):
        for c in hex_ring(k):
            yield c


def hex_to_pixel(q, r, size):
    """Flat-top hex layout -> pixel offset, used only by the presentation
    layer but kept here so the geometry has one definition."""
    x = size * 1.5 * q
    y = size * 1.7320508 * (r + q / 2.0)
    return x, y


class Galaxy:
    """Container for sectors plus adjacency and range queries."""

    def __init__(self):
        self.sectors = {}            # (q, r) -> Sector
        self.order = []              # stable iteration order

    def add(self, sector):
        self.sectors[sector.coord] = sector
        self.order.append(sector)
        return sector

    def get(self, coord):
        return self.sectors.get(coord)

    def __iter__(self):
        return iter(self.order)

    def __len__(self):
        return len(self.order)

    def neighbors(self, sector):
        out = []
        q, r = sector.coord
        for c in hex_neighbors(q, r):
            s = self.sectors.get(c)
            if s is not None:
                out.append(s)
        return out

    def within_range(self, sector, jump_range):
        """Breadth-first expansion limited to ``jump_range`` hops.

        Returns a list of (sector, hops) excluding the origin. The BFS cap is
        the performance guard that keeps trader routing affordable in a large
        galaxy.
        """
        seen = {sector.coord: 0}
        out = []
        dq = deque([(sector, 0)])
        while dq:
            cur, hops = dq.popleft()
            if hops >= jump_range:
                continue
            for nb in self.neighbors(cur):
                if nb.coord in seen:
                    continue
                seen[nb.coord] = hops + 1
                out.append((nb, hops + 1))
                dq.append((nb, hops + 1))
        return out

    def path(self, src, dst, max_hops=24):
        """Shortest hop path between sectors (inclusive of both ends)."""
        if src is dst:
            return [src]
        prev = {src.coord: None}
        dq = deque([(src, 0)])
        while dq:
            cur, hops = dq.popleft()
            if hops >= max_hops:
                continue
            for nb in self.neighbors(cur):
                if nb.coord in prev:
                    continue
                prev[nb.coord] = cur
                if nb is dst:
                    chain = [nb]
                    node = cur
                    while node is not None:
                        chain.append(node)
                        node = prev[node.coord]
                    chain.reverse()
                    return chain
                dq.append((nb, hops + 1))
        return []

    def owned_by(self, faction_key):
        return [s for s in self.order if s.owner == faction_key]

    def bounds(self):
        qs = [c[0] for c in self.sectors]
        rs = [c[1] for c in self.sectors]
        return min(qs), max(qs), min(rs), max(rs)
