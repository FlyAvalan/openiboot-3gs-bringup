#!/bin/bash
# Reproducible iPhone 3GS (S5L8920) openiboot build on a modern host via Docker.
# Produces: iphone_3gs_openiboot (ELF) and iphone_3gs_openiboot.bin (raw DFU-loadable image).
set -e
cd "$(dirname "$0")"

IMG=idroid-amd64
docker build --platform=linux/amd64 -f Dockerfile.amd64build -t "$IMG" .

docker run --rm --platform=linux/amd64 -v "$PWD":/src -e CROSS=arm-none-eabi- "$IMG" bash -c "
  git config --global --add safe.directory /src
  ${1:-scons iPhone3GS}
"

echo
echo '=== Artifacts ==='
ls -la iphone_3gs_openiboot iphone_3gs_openiboot.bin 2>/dev/null
