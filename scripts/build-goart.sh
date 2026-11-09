#!/usr/bin/env bash
# Build goart (go-atomicredteam, the Atomic Red Team runner the art board injects)
# in a Go container: a static linux binary at assets/art/goart-<arch>. Burger stages
# goart-arm64 on arm64 routes and plain goart otherwise (name an amd64 build goart).
# Needs docker and network (GitHub, for the source and the atomics it embeds).
# usage: scripts/build-goart.sh [arm64|amd64]
set -euo pipefail
arch="${1:-arm64}"
here="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
docker run --rm -v "$here/assets/art:/out" -e ARCH="$arch" -e OWNER="$(id -u):$(id -g)" \
  "${GOART_GO_IMAGE:-golang:1.22}" bash -ec '
    git clone -q --depth 1 https://github.com/activeshadow/go-atomicredteam /src && cd /src
    # the atomics are embedded into the binary (go:embed include/*) and used only when
    # REPO is set at link time (as the upstream Makefile does); otherwise goart fetches
    # them from GitHub at run time and fails on an air-gapped range. The download needs
    # unzip, so fail loudly when it did not land.
    command -v unzip >/dev/null || { apt-get update -qq && apt-get install -y -qq unzip >/dev/null; }
    ./download-atomics.sh >/dev/null
    test -s include/atomics/T1082/T1082.yaml
    CGO_ENABLED=0 GOOS=linux GOARCH=$ARCH go build -ldflags="-X actshad.dev/go-atomicredteam.REPO=redcanaryco/master -s -w" -trimpath -o /out/goart-$ARCH cmd/main.go
    chown $OWNER /out/goart-$ARCH'
ls -l "$here/assets/art/goart-$arch"
