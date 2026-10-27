# art assets

The `art` board injects the `goart` binary onto its target node at
`/phenix/art/goart`. The binary is not committed here (it is a prebuilt Atomic
Red Team runner, platform-specific and large).

Drop the Linux `goart` build in this directory as `goart`:

```
assets/art/goart
```

`burger run art` stages it from here; `burger check art` renders the board
without it. See `components/art.yaml` and `requires.goart` in the board.
