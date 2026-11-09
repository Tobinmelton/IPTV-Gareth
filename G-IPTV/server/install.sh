#!/usr/bin/env bash
# G-IPTV installer: creates a venv, installs deps, and puts a `giptv`
# command on your PATH so you can start the server from anywhere.
# Safe to re-run. Works on a fresh Linux PC with python3 + curl.
#
#   bash server/install.sh          (or: bash /full/path/server/install.sh)
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="$ROOT/server/.venv"
BIN_DIR="$HOME/.local/bin"

echo "== G-IPTV install =="
echo "   project: $ROOT"

# 1. python venv -------------------------------------------------------------
if [[ ! -x "$VENV/bin/python" ]]; then
  echo "-> creating virtualenv at $VENV"
  rm -rf "$VENV"
  python3 -m venv "$VENV"
fi

# venvs created on Debian/Ubuntu can ship without pip (ensurepip lives in
# the python3-venv package). Test functionally — a venv can have a broken
# or missing pip script while still looking "installed" as a file.
if ! "$VENV/bin/python" -m pip --version >/dev/null 2>&1; then
  echo "-> pip missing inside the venv; bootstrapping it"
  if ! "$VENV/bin/python" -m ensurepip --upgrade; then
    echo "-> ensurepip unavailable; fetching get-pip.py"
    curl -fsSL https://bootstrap.pypa.io/get-pip.py -o /tmp/get-pip.py
    "$VENV/bin/python" /tmp/get-pip.py
  fi
fi

# 2. dependencies ------------------------------------------------------------
echo "-> installing dependencies"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$ROOT/server/requirements.txt"

# 3. `giptv` launcher on PATH (self-healing: re-installs if venv missing) ----
mkdir -p "$BIN_DIR"
cat > "$BIN_DIR/giptv" <<EOF
#!/usr/bin/env bash
# G-IPTV launcher — safe to run from any directory.
VENV_PY="$VENV/bin/python"
if [[ ! -x "\$VENV_PY" ]]; then
  echo "[giptv] environment missing — running installer…" >&2
  bash "$ROOT/server/install.sh"
fi
exec "\$VENV_PY" "$ROOT/server/main.py" "\$@"
EOF
chmod +x "$BIN_DIR/giptv"

# 4. PATH — append ~/.local/bin in .profile and .bashrc if not present -------
for rc in "$HOME/.profile" "$HOME/.bashrc"; do
  [[ -f "$rc" ]] || touch "$rc"
  if ! grep -q 'HOME/.local/bin' "$rc"; then
    printf '\n# G-IPTV\nexport PATH="$HOME/.local/bin:$PATH"\n' >> "$rc"
    echo "-> PATH line added to $rc"
  fi
done

echo
echo "== Done =="
echo "   Start the server from anywhere with:   giptv"
echo "   (open a NEW terminal, or: source ~/.bashrc)"
echo "   Then open:  http://<this-pc-ip>:8484"
