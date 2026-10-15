"""
Render every screen off-screen and save a PNG per view.

Lets the whole presentation layer be exercised (and visually checked) without
a display, and catches rendering exceptions in CI-style runs.

    python uitest.py [scenario] [ticks]
"""

import os
import sys

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pygame  # noqa: E402


def main(scenario="sandbox", ticks=120, outdir="screenshots"):
    from cosmic_balance.ui.app import App
    from cosmic_balance.ui import theme as T

    os.makedirs(outdir, exist_ok=True)
    app = App(scenario)
    for _ in range(ticks):
        app.world.step()

    # Give the views something selected to render.
    ctx = app.ctx
    ctx.sector = max(app.world.galaxy, key=lambda s: len(s.stations))
    ctx.station = ctx.sector.stations[0] if ctx.sector.stations else None
    ctx.fleet = app.world.fleets[0] if app.world.fleets else None
    ctx.commodity = "FOOD"
    ctx.notify("example order issued")

    failures = []
    for i, name in enumerate(app.TAB_NAMES):
        app.current = i
        try:
            app.draw()
            pygame.display.flip()
            path = os.path.join(outdir, f"{i}_{name.lower()}.png")
            pygame.image.save(app.screen, path)
            print(f"ok   {name:<12} -> {path}")
        except Exception as exc:                        # noqa: BLE001
            import traceback
            failures.append((name, exc))
            print(f"FAIL {name:<12} {type(exc).__name__}: {exc}")
            traceback.print_exc()

    # also exercise the help overlay and the map overlays
    try:
        app.current = 0
        app.show_help = True
        app.draw()
        pygame.image.save(app.screen, os.path.join(outdir, "help.png"))
        app.show_help = False
        for overlay in ("piracy", "price", "people", "industry", "supply"):
            ctx.overlay = overlay
            app.draw()
            pygame.image.save(app.screen,
                              os.path.join(outdir, f"map_{overlay}.png"))
        print("ok   overlays + help")
    except Exception as exc:                            # noqa: BLE001
        import traceback
        failures.append(("overlays", exc))
        traceback.print_exc()

    pygame.quit()
    if failures:
        print(f"\n{len(failures)} screen(s) failed")
        return 1
    print(f"\nall {len(app.TAB_NAMES)} screens rendered cleanly")
    return 0


if __name__ == "__main__":
    name = sys.argv[1] if len(sys.argv) > 1 else "sandbox"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 120
    sys.exit(main(name, n))
