"""
PROJECT COSMIC BALANCE -- entry point.

    python main.py                 # the verification sandbox (7 sectors)
    python main.py galaxy          # the full campaign  (61 sectors)
    python main.py sandbox 12345   # a specific world seed
    python main.py --list          # show available scenarios

Headless tools, for balancing and for proving the engine runs without any
presentation layer at all:

    python headless.py sandbox 500
    python diagnose.py galaxy 200
"""

import sys


def usage():
    from cosmic_balance.sim.scenarios import SCENARIOS
    print(__doc__)
    print("Scenarios:")
    for key, (title, blurb, _) in SCENARIOS.items():
        print(f"  {key:<10} {title}")
        print(f"             {blurb}")


def main(argv):
    args = [a for a in argv[1:] if not a.startswith("-")]
    if "--list" in argv or "-l" in argv or "--help" in argv or "-h" in argv:
        usage()
        return 0

    scenario = args[0] if args else "sandbox"
    seed = int(args[1]) if len(args) > 1 else None

    from cosmic_balance.sim.scenarios import SCENARIOS
    if scenario not in SCENARIOS:
        print(f"unknown scenario {scenario!r}")
        usage()
        return 2

    try:
        import pygame                                        # noqa: F401
    except ImportError:
        print("pygame is required for the game window:\n"
              "    python -m pip install pygame\n"
              "The simulation itself runs without it: python headless.py")
        return 1

    from cosmic_balance.ui.app import main as run
    run(scenario, seed)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
