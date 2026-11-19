#!/usr/bin/env bash
# 1.35.0's name for the catch-up hook. Since 1.36.0 the whole job is repo-fresh.sh (the fetch, the catch-up, the
# "behind" warning and the session channel's doorbell), and this file stays only so a session started on 1.35.0, whose
# hooks.json asks the loader for this name, runs the one hook and never two.
. "$(dirname "${BASH_SOURCE[0]}")/repo-fresh.sh"
