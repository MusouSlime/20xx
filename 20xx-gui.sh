#!/usr/bin/env bash
# Launch the 20XX GUI (Linux / SteamOS / macOS).
# Double-click in a file manager, or run ./20xx-gui.sh
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
exec "$DIR/tools/20xx/20xx" gui
