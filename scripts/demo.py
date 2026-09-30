"""
Live / recorded demo of the conversational layer. Run from the repo root.

    python3 scripts/demo.py               # scripted session, press Enter to send each question
    python3 scripts/demo.py --auto        # no pauses (for screen recording or a smoke test)
    python3 scripts/demo.py --interactive # scripted session, then free chat
"""
import argparse
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "src"))
sys.path.insert(0, HERE)
import explain as E  # noqa: E402
from run_submission import session_scripts  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--auto", action="store_true")
    ap.add_argument("--interactive", action="store_true")
    a = ap.parse_args()

    print("Loading data...")
    data = E.Data()
    s = E.Session(data)
    for msg in session_scripts(data)["session_1_escalation"]:
        if not a.auto:
            input("\n[Enter to send the next question] ")
        print("\n> " + msg)
        prev = s.stats
        print(s.ask(msg))
        print("\n  (%s | filter=%s)" % ("recomputed" if s.stats is not prev else "no recompute", s.filter))
    if a.interactive:
        print("\nFree chat. Ctrl-C to quit.")
        while True:
            try:
                print("\n" + s.ask(input("\n> ")))
            except (KeyboardInterrupt, EOFError):
                break


if __name__ == "__main__":
    main()
