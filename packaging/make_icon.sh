#!/bin/sh
# Regenerate packaging/20xx.png + 20xx.ico from packaging/20xx.svg.
# Requires ImageMagick (`convert`). The outputs are committed, so normal
# builds do not need ImageMagick.
set -eu
DIR="$(cd "$(dirname "$0")" && pwd)"
convert -background none "$DIR/20xx.svg" -resize 512x512 -depth 8 "$DIR/20xx.png"
convert "$DIR/20xx.png" -define icon:auto-resize=256,128,64,48,32,16 "$DIR/20xx.ico"
echo "wrote $DIR/20xx.png and $DIR/20xx.ico"
