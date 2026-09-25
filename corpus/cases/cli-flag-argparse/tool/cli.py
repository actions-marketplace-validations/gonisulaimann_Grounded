import argparse


def main(argv=None):
    p = argparse.ArgumentParser(prog="tool")
    sub = p.add_subparsers(dest="cmd")
    run = sub.add_parser("run")
    run.add_argument("--verbose", action="store_true")
    run.add_argument("--output", "-o")
    run.add_argument("--color", action=argparse.BooleanOptionalAction)
    return p.parse_args(argv)
