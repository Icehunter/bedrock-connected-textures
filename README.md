# Bedrock Connected Textures

Java texture pack features on Minecraft Bedrock: connected textures, big
repeating patterns, random textures, overlays, 3D leaves and plants, 3D held
items, custom mob models and PBR materials, in Classic, Vibrant Visuals and
ray tracing.

It has three parts:

- **The engine**: a Bedrock add-on. Players install it once.
- **The converter**: a tool for pack makers. It turns a Java texture pack into
  one Bedrock add-on that works with the engine.
- **bct.py**: tools for Bedrock pack authors. Your own Bedrock pack gets the
  same features, written in plain Bedrock files.

## For players

1. Add **Bedrock Connected Textures** to your world (Behavior Packs).
2. Add a pack made for it, and turn on both its behavior pack and its
   resource pack.
3. Play. Blocks take the pack's look as soon as they load, nearest first,
   and the blocks you place change at once. The world beyond your simulation
   distance is converted in the background, one area at a time; the action
   bar shows how many of the chunks around you are done.

The same pack works in Classic, Vibrant Visuals and ray tracing; there is no
setting to pick.

The engine swaps blocks for look-alike copies that can show the pack's
patterns, and they stay swapped. They work like the normal blocks: mining,
drops, tools, explosions and redstone behave the same. Grass, dirt, sand and
other blocks whose gameplay needs the real block stay vanilla; their random
textures, edges and decals are drawn by the game itself.

- Converted blocks stay in the world: a world played with a converted pack
  keeps needing it. Try a pack in a copy of your world first.
- `/scriptevent bct:control status` shows what the engine is doing.

## For pack makers

[docs/PACK-MAKERS.md](docs/PACK-MAKERS.md) explains what converts, how to run
the converter and how your pack's name, credits and version are kept.

In short: unzip the converter, then drag your Java pack ZIP (and, if you have
one, your own Bedrock `.mcpack` for its Vibrant Visuals lighting) onto
`Convert-Java-Pack.cmd`. The converted pack keeps your name, version,
resolution, description and credits.

## For Bedrock pack authors

[docs/AUTHORING.md](docs/AUTHORING.md) is the guide: patterns, 3D leaves,
edges, overlays and connected textures in a pack you write for Bedrock, with
no Java pack and no OptiFine files. Your behavior pack exports its data from
`scripts/bct.js`; `bct.py`, run from a clone of this repository, writes the
custom blocks with the vanilla block's gameplay filled in and checks the pack:

```
python bct.py init mypack --bp MyPack_BP --rp MyPack_RP
python bct.py block minecraft:stone mypack:stone --repeat 3 3 --grid stone_grid.png --bp MyPack_BP --rp MyPack_RP
python bct.py check --bp MyPack_BP --rp MyPack_RP
```

## For developers

| Path | Contents |
| --- | --- |
| `engine/` | The engine's scripts. `identity.json` holds its fixed pack UUIDs and version. |
| `converter/` | The converter (Python, plus one Node script) and the packagers. `converter/data/` holds policy tables. |
| `tests/` | Python `unittest` and Node `node:test` suites, with fixtures. |
| `bct.py` | The pack authors' command-line tool (`converter/authoring.py`, `converter/check_pack.mjs`). |
| `docs/` | [ENGINE.md](docs/ENGINE.md) (how the engine works), [CONVERTER.md](docs/CONVERTER.md) (converter internals and options), [AUTHORING.md](docs/AUTHORING.md) and [bct.schema.json](docs/bct.schema.json) (Bedrock pack authors). |

Setup:

```
python -m pip install -r requirements.txt
```

Node 18 or later runs the engine tests and the converter's tile selector.

Mojang's [bedrock-samples](https://github.com/Mojang/bedrock-samples) are needed
for conversions and some tests. They are not included. Put them next to this
folder as `../bedrock-samples`, set `BEDROCK_SAMPLES`, or pass `--samples`. The
converter launcher downloads them on first run.

Tests:

```
python -m unittest discover -s tests -p "test_*.py"
node --test tests/*.mjs
```

Common commands:

```
# The engine add-on
python converter/engine_package.py --output dist

# Convert a Java pack (one .mcaddon for the engine); --vv-scene takes the author's
# Bedrock lighting, --bedrock-grade also their Bedrock colour grade
python converter/convert_java_author_pack.py --java "Pack R1.0 256x.zip" --output converted/pack --key pack \
    --vv-scene "Pack Bedrock.mcpack"

# The converter ZIP for pack makers
python converter/package_converter.py
```

## Licence

The engine and the converter are GPL-3.0 (`LICENSE`), with one additional
permission (`LICENSE-EXCEPTION.md`, summarised in `NOTICE`): everything the
converter writes into a converted pack may be distributed under any terms,
closed-source or paid, even though some of it (such as the publisher script)
is copied from this program. Changes to the engine or the converter
themselves stay under the GPL. `THIRD-PARTY-NOTICES.md` covers Mojang data.

### Converting other people's packs

The licence above covers this program only. It gives no rights in the pack
you convert: its art, models and sounds belong to its author. Converting a
pack you are allowed to use, for your own play, is fine. Only the pack's
author, or someone with the author's permission, should share or sell a
converted pack.
