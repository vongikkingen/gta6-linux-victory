#!/bin/bash
# Security gate before building community code: what runs on the machine during the build
# (build.rs, procedural macros, dependencies, cargo config) and the risky calls added.
# Usage: scan-securite.sh <base> <head>   e.g. scan-securite.sh main matrix2
# Exit code 0: nothing blocking; 1: file executed at build time or third-party dependency changed
# (review before compiling). Risky calls are listed for review, without blocking.
set -u
PORT=${GTA6_PORT:-$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)}
REPO=${GTA6_REPO:-$PORT/src/victory}
REPO_ORIG=$REPO
REPO=$(cd "$REPO" 2>/dev/null && pwd) || { echo "repository not found: $REPO_ORIG" >&2; exit 2; }
B=${1:?base}; H=${2:?head}
cd "$REPO" || exit 2
R="$B...$H"
echo "== $R: $(git diff --shortstat "$R")"
block=0
s=$(git diff --name-only "$R" | grep -E '(^|/)(build\.rs|\.cargo/|rust-toolchain(\.toml)?|xtask/|\.github/)|\.(sh|ps1|bat|py|exe|dll|so|bin)$')
[ -n "$s" ] && { echo "-- executed at build time or binaries:"; echo "$s" | sed 's/^/   /'; block=1; }
p=$(git diff "$R" -- '*Cargo.toml' | grep -E '^\+.*proc-macro')
[ -n "$p" ] && { echo "-- procedural macro added:"; echo "$p" | sed 's/^/   /'; block=1; }
d=$(git diff "$R" -- Cargo.lock | grep -E '^\+(name|source) = ' | grep -vE 'name = "victory' )
[ -n "$d" ] && { echo "-- new or changed third-party dependencies:"; echo "$d" | sed 's/^/   /'; block=1; }
echo "-- risky calls added (to review):"
git diff -U0 "$R" -- '*.rs' | awk '/^\+\+\+ /{f=substr($2,3)} /^@@/{split($3,a,","); l=substr(a[1],2)-1} /^\+[^+]/{l++; print f":"l": "substr($0,2)} /^ /{l++}' |
  grep -E 'std::process|Command::new|std::net|TcpStream|UdpSocket|reqwest|ureq|https?://|CreateProcess|ShellExecute|WinExec|URLDownload|Internet(Open|Connect)|WinHttp|WSAStartup|home_dir|\.ssh|base64|include_bytes!|global_asm!|PAGE_EXECUTE|RegSetValue|RegCreateKey' |
  grep -vE '^\S+:[0-9]+: *//' | cut -c1-200 | sed 's/^/   /'
[ $block = 0 ] && echo "mechanical verdict: nothing blocking" || echo "mechanical verdict: REVIEW before build"
exit $block
