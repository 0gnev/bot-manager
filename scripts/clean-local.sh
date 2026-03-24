#!/usr/bin/env bash
set -euo pipefail

find data/state -mindepth 1 ! -name '.gitkeep' -exec rm -rf {} +
find data/uploads -mindepth 1 ! -name '.gitkeep' -exec rm -rf {} +
find data/knowledge -mindepth 1 ! -name '.gitkeep' -exec rm -rf {} +

echo "Local runtime data cleaned."
