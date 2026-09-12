#!/bin/sh
# Install the pre-commit guard. Run once after cloning.
#   sh install-hooks.sh
set -e
mkdir -p .git/hooks
cp tools/pre-commit .git/hooks/pre-commit
chmod +x .git/hooks/pre-commit
echo "pre-commit guard installed."
