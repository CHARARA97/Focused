#!/usr/bin/env bash
#
# Build the AUR source archive from HEAD and refresh the checksum.
#
#   scripts/make-aur-archive.sh [--package focused|focused-git]
#
# Why this exists: makepkg builds the *archive*, not the working tree, and
# `git archive HEAD` reads committed content only.  Two mistakes are easy to make
# and both produce a package built from the wrong source:
#
#   * archiving before committing (the change is not in HEAD yet);
#   * writing the archive somewhere makepkg does not look -- `git -C <dir> archive`
#     resolves -o relative to <dir>, not to where you typed the command.
#
# This script resolves the repository, reads the version from pyproject.toml, writes
# the archive where the PKGBUILD expects it, verifies it contains what check() needs,
# and refreshes .SRCINFO.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/.." && pwd)"
PACKAGE="focused"

while [ "$#" -gt 0 ]; do
    case "$1" in
        --package) PACKAGE="$2"; shift 2 ;;
        -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
        *) echo "unknown argument: $1" >&2; exit 2 ;;
    esac
done

say()  { printf '[aur] %s\n' "$*"; }
die()  { printf '[aur] %s\n' "$*" >&2; exit 1; }

[ -d "$ROOT/.git" ] || die "$ROOT is not a git repository"
command -v git >/dev/null || die "git is required"

VERSION="$(sed -n 's/^version *= *"\([^"]*\)".*/\1/p' "$ROOT/pyproject.toml" | head -1)"
[ -n "$VERSION" ] || die "could not read the version from pyproject.toml"

REPO_NAME="$PACKAGE"
ARCHIVE_DIR="$ROOT/packaging/aur/$PACKAGE"
ARCHIVE="$ARCHIVE_DIR/$PACKAGE-$VERSION.tar.gz"
[ -d "$ARCHIVE_DIR" ] || die "no such package directory: $ARCHIVE_DIR"

# Committed content only: warn about anything that would silently be left out.
DIRTY="$(git -C "$ROOT" status --porcelain -- . | head -20)"
if [ -n "$DIRTY" ]; then
    say "note: these paths are not committed and will NOT be in the archive:"
    printf '%s\n' "$DIRTY" | sed 's/^/  /'
    say "commit them first if they belong in the package"
fi

say "archiving HEAD as $REPO_NAME-$VERSION/ -> $ARCHIVE"
git -C "$ROOT" archive --format=tar.gz --prefix="$REPO_NAME-$VERSION/" -o "$ARCHIVE" HEAD

# The archive has to contain everything check() and package() reach for.  The listing
# is read into a variable and matched with a shell pattern on purpose: piping it into
# `grep -q` would make grep exit early, tar would die of SIGPIPE, and pipefail would
# then report "missing" for a file that is right there.
LISTING="$(tar -tzf "$ARCHIVE"; echo)"
for required in scripts/test-runner.py scripts/doctor-focused.sh scripts/launch-focused.sh packaging/focused.service; do
    if [[ "$LISTING" != *"/$required"$'\n'* ]]; then
        die "$ARCHIVE is missing $required (wrong commit, or the file is untracked)"
    fi
done
say "archive verified: $(du -h "$ARCHIVE" | cut -f1)"

if command -v updpkgsums >/dev/null; then
    ( cd "$ARCHIVE_DIR" && updpkgsums )
    say "checksum refreshed"
else
    say "updpkgsums not found: run it yourself before building"
fi

if command -v makepkg >/dev/null; then
    ( cd "$ARCHIVE_DIR" && makepkg --printsrcinfo > .SRCINFO )
    say ".SRCINFO refreshed"
fi

say "next: cd $ARCHIVE_DIR && makepkg -si"
