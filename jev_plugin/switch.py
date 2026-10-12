"""show or change the plugin's switches.

    jev-switch                     list every switch
    jev-switch <name> on|off       change one, e.g. after_call.relevance off
"""

import sys

from . import config


def show(cfg):
    for name, what in config.DESCRIPTIONS.items():
        state = "on " if config.on(cfg, name) else "off"
        print("%s  %-24s %s" % (state, name, what))
    print("config file: %s" % config.path())


def main(argv):
    if not argv:
        show(config.load())
        return 0
    if len(argv) != 2 or argv[1] not in ("on", "off"):
        print(__doc__.strip(), file=sys.stderr)
        return 2
    try:
        cfg = config.set_switch(argv[0], argv[1] == "on")
    except KeyError:
        print("unknown switch %r. known: %s" % (argv[0], ", ".join(config.DESCRIPTIONS)), file=sys.stderr)
        return 2
    show(cfg)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
