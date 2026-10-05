# Third-party notices

## Formats

The tile selection in `engine/tiles.mjs`, `engine/tile-template.mjs` and `engine/overlay.mjs`, and the compact-tile expansion in `converter/compact_tiles.py`, implement the public OptiFine `ctm.properties` format. They were written for this project and contain no code from OptiFine or Continuity. Random tile selection reproduces the results of Continuity's position hash so converted packs place the same variants; the test vectors in `tests/fixtures/` were produced independently.

## Mojang bedrock-samples

The build reads Mojang's [bedrock-samples](https://github.com/Mojang/bedrock-samples) for block lists, vanilla textures and particle files. This repository does not include them. They are under Mojang's terms and the [Minecraft EULA](https://www.minecraft.net/en-us/eula). Packs built from them carry those terms for any Mojang files they contain.

## Java client reference

The converter downloads a Mojang Java client JAR for vanilla models and biome data, checked against Mojang's published hash. It is not stored in this repository.

## Artwork

`converter/data/artwork/` holds the grass and sand edge cutouts and the pack icon, made with an image generation tool. `PROVENANCE.md` there records the prompts.

## Converted packs

A converted pack holds images taken from the source Java pack. Sharing it depends on that pack's licence.
