import { Code, CodeBlock, ExtLink, PageHeader, PairTable, Section } from "../components/ui";
import { COMMANDS, REPO, SETTINGS } from "../site";

const VARIATIONS = `{
  "resource_pack_name": "my_pack",
  "texture_name": "atlas.terrain",
  "texture_data": {
    "my_stone": {
      "textures": {
        "variations": [
          { "path": "textures/blocks/my_stone_a", "weight": 3 },
          { "path": "textures/blocks/my_stone_b", "weight": 1 }
        ]
      }
    }
  }
}`;

const QUICK_START = `python bct.py init mypack --bp MyPack_BP --rp MyPack_RP
python bct.py block minecraft:stone mypack:stone --repeat 3 3 --grid stone_grid.png --bp MyPack_BP --rp MyPack_RP
python bct.py check --bp MyPack_BP --rp MyPack_RP`;

const BCT_JS = `export default {
  format: 1,
  pack: "mypack",
  blocks: { "minecraft:stone": { block: "mypack:stone", pattern: { repeat: [3, 3] } } },
  leaves: { "minecraft:oak_leaves": { block: "mypack:oak_leaves", models: { near: [2, 2, 1], far: [1] } } },
  edges: [{ from: "minecraft:grass_block", onto: ["minecraft:stone"], block: "mypack:grass_edge" }],
  connected: { "minecraft:glass": { block: "mypack:glass", open: true } }
};`;

export default function Developers() {
  return (
    <>
      <PageHeader
        title="For Bedrock pack authors and developers"
        lead="Make your own Bedrock pack work with BCT, and how the engine receives its data, what it scans and how to tune it."
      />

      <Section id="overview" title="How the engine works">
        <p>
          The engine is one behavior pack, <Code>BCT_BP</Code>. Its header and module UUIDs are fixed
          and never change between builds. It needs Bedrock 1.26.50 (26.50 in the game's own version
          name) and stable <Code>@minecraft/server</Code> 2.10, with no beta APIs.
        </p>
        <p>
          The engine reads no <Code>.properties</Code> files at runtime. The converter turns Java rules
          into data, and each converted pack's behavior pack sends that data to the engine.
        </p>
        <ol className="list-decimal space-y-3 pl-6">
          <li>
            <strong>The publisher script.</strong> A converted pack's <Code>scripts/source-data.js</Code>{" "}
            holds one packet per part (<Code>connected</Code>, <Code>terrain</Code> and{" "}
            <Code>replace</Code>), named by the pack key, with a checksum. The publisher script sends
            the packets in chunks of 750 characters until the engine acknowledges them, and again
            whenever the engine restarts. The converted pack's manifest lists the engine as a
            dependency, so Bedrock links them.
          </li>
          <li>
            <strong>Several packs at once.</strong> Each keeps its own entity types and data. When two
            packs draw the same block, the one with the higher <Code>sourcePriority</Code> (then key
            order) owns it.
          </li>
          <li>
            <strong>The scan.</strong> A chunk scanner both parts share makes one <Code>getBlocks</Code>{" "}
            call with every block type that some part wants in that chunk. Scans run as jobs under a
            per-tick time budget. Chunks are worked nearest first and scanned again when a player moves
            more than half the band up or down.
          </li>
          <li>
            <strong>The drawing.</strong> Native replacement blocks stand in for vanilla blocks near
            players and carry vanilla gameplay. Leaf model blocks draw the pack's own leaf models.
            Overlay surfaces are thin custom blocks in the empty cell in front of a face. Connected
            textures draw on passive entity carriers, which cannot draw in ray tracing, so the engine
            removes them when every player uses ray tracing.
          </li>
        </ol>
        <p>
          The full description is in <a className="text-accent underline underline-offset-4" href="#/docs/engine">the engine doc</a>.
          Source is in the <ExtLink href={`${REPO}/tree/main/engine`}>engine folder</ExtLink> of the repository.
        </p>
      </Section>

      <Section id="components" title="Components the engine provides">
        <PairTable
          label="Custom components"
          head={["Component", "What it does"]}
          mono
          rows={[
            ["bct:update_repeat", "Block component for native repeat blocks. It keeps each block's place in the pattern as coordinate states."],
            ["bct:leaf", "Block component for leaf model blocks. On a random tick, a marked leaf decays if no log its leaves reach is within 4 blocks, and is unmarked otherwise. Persistent leaves never decay."],
          ]}
        />
        <p>
          Leaf model blocks mirror <Code>persistent_bit</Code> and <Code>update_bit</Code> exactly and
          add <Code>bct:t</Code> (the weighted model turn) and <Code>bct:look</Code> (the model the
          author's blockstate shows for the leaf's distance and persistence).
        </p>
      </Section>

      <Section id="settings" title="Engine settings">
        <p>
          Change them in the world with <Code>/scriptevent bct:config</Code>. It merges the values you
          give over the defaults, rejects unknown names and out-of-range values, and saves them in the
          world.
        </p>
        <CodeBlock label="Example config command">{`/scriptevent bct:config {"connected":{"chunkRadius":4,"maxCarriers":2000}}`}</CodeBlock>
        <PairTable label="Engine settings and defaults" head={["Setting", "Default"]} rows={SETTINGS} mono />
      </Section>

      <Section id="commands" title="Commands">
        <PairTable label="Engine commands" head={["Command", "What it does"]} rows={COMMANDS} mono />
        <p>With <Code>debug</Code> off, the engine writes nothing to the log.</p>
      </Section>

      <Section id="authoring" title="Make your Bedrock pack work with BCT">
        <p>
          Since 1.1, a pack written for Bedrock can do what Java packs do with OptiFine or Continuity,
          in plain Bedrock files: no Java pack, no OptiFine rules and no conversion. Your behavior pack
          exports its data from <Code>scripts/bct.js</Code>, and <Code>bct.py</Code>, run from a clone
          of the repository, writes the custom blocks with the vanilla block's gameplay filled in and
          checks the pack before you load a world.
        </p>
        <PairTable
          label="What a Bedrock pack can do with BCT"
          head={["Feature", "What players see"]}
          rows={[
            ["Random variants", "Weighted random textures. Plain Bedrock, no BCT needed."],
            ["Patterns", "Fixed repeats (a 3x3 stone pattern) and random tiles that never turn, in every graphics mode."],
            ["3D leaves", "Your leaf models in place of vanilla leaves, with vanilla decay and drops."],
            ["Edges", "Grass, sand or any ground spreading onto the tops of blocks beside it."],
            ["Overlays", "The 17-tile overlay template on any face, as Java overlays pick it."],
            ["Connected blocks", "Glass and other blocks joining their neighbours, drawn by the block itself, ray tracing included."],
            ["Carriers", "The full 47-tile connected look, inner corners included, in Classic and Vibrant Visuals."],
          ]}
        />
        <CodeBlock label="A 3x3 stone pattern, from nothing" language="bash">{QUICK_START}</CodeBlock>
        <CodeBlock label="scripts/bct.js" language="js">{BCT_JS}</CodeBlock>
        <p>
          <a className="text-accent underline underline-offset-4" href="#/docs/authoring">The Bedrock pack guide</a>{" "}
          covers every feature, the art each one needs, the data file, checking and testing, and the
          limits. <Code>docs/bct.schema.json</Code> describes every field of the data.
        </p>
        <h3 className="pt-2 text-lg font-semibold">Random variants need no BCT</h3>
        <p>
          Bedrock's <Code>terrain_texture.json</Code> supports weighted <Code>variations</Code> on its
          own, for every block, with or without BCT:
        </p>
        <CodeBlock label="terrain_texture.json with weighted variations" language="json">{VARIATIONS}</CodeBlock>
      </Section>
    </>
  );
}
