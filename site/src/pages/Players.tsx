import { Alert, Accordion } from "@heroui/react";
import { LinkButton, PageHeader, Section, Code, CodeBlock } from "../components/ui";
import { LATEST } from "../site";

export default function Players() {
  return (
    <>
      <PageHeader
        title="For players"
        lead="Install the engine once. Then add any pack made for it. The same pack works in Classic, Vibrant Visuals and ray tracing."
      />

      <Alert status="warning" className="mb-10 max-w-3xl">
        <Alert.Indicator />
        <Alert.Content>
          <Alert.Title>Try a pack in a copy of your world first</Alert.Title>
          <Alert.Description>
            Converted blocks stay in the world. A world played with a converted pack keeps needing it.
          </Alert.Description>
        </Alert.Content>
      </Alert>

      <Section id="requirements" title="What you need">
        <ul className="list-disc space-y-1 pl-6">
          <li>Minecraft Bedrock 26.50 or newer.</li>
          <li>The Bedrock Connected Textures engine add-on, from the latest release.</li>
          <li>A pack made for it, converted from a Java texture pack.</li>
        </ul>
        <LinkButton href={LATEST}>Download the engine</LinkButton>
      </Section>

      <Section id="install" title="Install">
        <ol className="list-decimal space-y-3 pl-6">
          <li>
            Download the engine add-on (<Code>.mcaddon</Code>) from the latest release and open it.
            Minecraft imports it.
          </li>
          <li>Download a converted pack (<Code>.mcaddon</Code>) and open it the same way.</li>
          <li>
            Create a world, or open a copy of an existing one. Under <strong>Behavior Packs</strong>,
            add <strong>Bedrock Connected Textures</strong>. Add your converted pack too and turn on
            both its behavior pack and its resource pack. The converted pack depends on the engine, so
            Bedrock turns the engine on with it.
          </li>
          <li>
            Play. There is no graphics setting to pick: the pack works in Classic, Vibrant Visuals and
            ray tracing.
          </li>
        </ol>
        <p>
          Several converted packs can be on at the same time. The engine draws them all under one set
          of limits.
        </p>
      </Section>

      <Section id="converting" title="What you will see">
        <p>
          Blocks take the pack's look as soon as they load, nearest first. The blocks you place change
          at once. The world beyond your simulation distance is converted in the background, one area
          at a time. The action bar shows how far it has got:
        </p>
        <CodeBlock label="Action bar text">{"Converting the world around you: N / M chunks"}</CodeBlock>
        <p>
          The engine swaps blocks for look-alike copies that can show the pack's patterns. They work
          like the normal blocks: mining, drops, tools, explosions and redstone behave the same. Grass,
          dirt, sand and other blocks whose gameplay needs the real block stay vanilla. Their random
          textures, edges and decals are drawn by the game itself.
        </p>
      </Section>

      <Section id="status" title="Check what the engine is doing">
        <p>With commands on, run this in the chat:</p>
        <CodeBlock label="Status command">/scriptevent bct:control status</CodeBlock>
        <p>
          Other commands, such as <Code>off</Code> and <Code>on</Code>, are listed on the{" "}
          <a className="text-accent underline underline-offset-4" href="#/developers">developers page</a>.
        </p>
      </Section>

      <Section id="faq" title="Good to know">
        <Accordion className="w-full max-w-3xl">
          <Accordion.Item id="stay">
            <Accordion.Heading>
              <Accordion.Trigger>
                Do converted blocks stay in my world?
                <Accordion.Indicator />
              </Accordion.Trigger>
            </Accordion.Heading>
            <Accordion.Panel>
              <Accordion.Body>
                Yes. Converted blocks stay in the world, so a world played with a converted pack keeps
                needing it. Try a pack in a copy of your world first. To take the engine out of a world,
                run <Code>/scriptevent bct:control off</Code> first.
              </Accordion.Body>
            </Accordion.Panel>
          </Accordion.Item>
          <Accordion.Item id="warning">
            <Accordion.Heading>
              <Accordion.Trigger>
                Minecraft warns about low resources. Is that a problem?
                <Accordion.Indicator />
              </Accordion.Trigger>
            </Accordion.Heading>
            <Accordion.Panel>
              <Accordion.Body>
                High-resolution packs exceed Bedrock's built-in pack memory budget, whatever your GPU
                is. The game shows a low-resource warning. It is harmless on PC.
              </Accordion.Body>
            </Accordion.Panel>
          </Accordion.Item>
          <Accordion.Item id="modes">
            <Accordion.Heading>
              <Accordion.Trigger>
                Does everything draw in every graphics mode?
                <Accordion.Indicator />
              </Accordion.Trigger>
            </Accordion.Heading>
            <Accordion.Panel>
              <Accordion.Body>
                Repeat, random and fixed blocks, overlays, leaves and held items draw in Classic,
                Vibrant Visuals and ray tracing. Connected methods (ctm, horizontal, vertical, top) draw
                in Classic and Vibrant Visuals. In ray tracing those blocks show the pack's plain texture.
              </Accordion.Body>
            </Accordion.Panel>
          </Accordion.Item>
          <Accordion.Item id="update">
            <Accordion.Heading>
              <Accordion.Trigger>
                A pack did not update after I installed a new version.
                <Accordion.Indicator />
              </Accordion.Trigger>
            </Accordion.Heading>
            <Accordion.Panel>
              <Accordion.Body>
                Bedrock only replaces an installed pack when its version goes up. To test the same
                version again, delete the old one first (Settings, then Storage).
              </Accordion.Body>
            </Accordion.Panel>
          </Accordion.Item>
        </Accordion>
      </Section>
    </>
  );
}
