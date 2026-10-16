import sys

from burger import __version__


def main(argv=None):
    args = sys.argv[1:] if argv is None else argv
    if args == ["--version"]:
        print(f"burger {__version__}")
        return 0
    print("usage: burger --version", file=sys.stderr)
    return 2
