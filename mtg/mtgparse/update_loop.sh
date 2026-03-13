#!/usr/bin/env bash

set -eo pipefail

DOCS="$(dirname -- "${BASH_SOURCE[0]}")/../../docs"
COMMIT_MESSAGE="${1:-auto update loop}"
TARGET_BRANCH=main

git commit --allow-empty -m "${COMMIT_MESSAGE}"

while true; do
  python -m mtgparse.process_manifest

  if [[ "$(git status --porcelain --untracked-files=no -- "${DOCS}")" ]]; then
    git add -- "${DOCS}"
    git commit --amend -m "${COMMIT_MESSAGE}"
    git push -f origin "HEAD:refs/heads/${TARGET_BRANCH}"
  fi
  
  sleep 5m
done
