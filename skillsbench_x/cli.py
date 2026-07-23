#!/usr/bin/env python3
"""skillsbench-x: unified CLI for the skill-eval research pipeline.

Three subcommands wrap the three stages:

    skillsbench-x rollout   ...   # delegates to skillsbench_x.rollout
    skillsbench-x judge     ...   # delegates to skillsbench_x.judge
    skillsbench-x aggregate ...   # delegates to skillsbench_x.aggregate

Subcommand args are forwarded verbatim to the underlying module's argparse.
"""

from __future__ import annotations

import sys

from . import aggregate as _aggregate
from . import judge as _judge
from . import rollout as _rollout


USAGE = """\
skillsbench-x <command> [args...]

Commands:
  rollout    Run agents in Docker via BenchFlow; capture trajectories.
  judge      LLM-as-judge over captured trajectories; per-phase JSON output.
  aggregate  Print per-task / per-phase scores with cascade gating.

Pass `<command> --help` to see flags for that command.
"""


def main() -> int:
    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help"):
        print(USAGE)
        return 0
    cmd = sys.argv[1]
    sys.argv = [f"skillsbench-x {cmd}"] + sys.argv[2:]
    if cmd == "rollout":
        return _rollout.main()
    if cmd == "judge":
        return _judge.main()
    if cmd == "aggregate":
        return _aggregate.main()
    print(f"unknown command: {cmd}\n\n{USAGE}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main())
