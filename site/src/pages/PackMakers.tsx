import { Alert } from "@heroui/react";
import { CodeBlock, Code, PageHeader, PairTable, Section, ExtLink, LinkButton } from "../components/ui";
import { CONVERTS, NOT_YET, OPTIONS, LATEST, REPO } from "../site";

export default function PackMakers() {
  return (
    <>
      <PageHeader
        title="For pack makers"
        lead="The converter turns a Java texture pack into one Bedrock add-on that works with the engine. It runs on your own computer. No account, upload or AI service is involved."
      />

      <Section id="needs" title="What you need">
        <ul className="list-disc space-y-1 pl-6">
          <li>Windows 10 or 11.</li>
          <li>
            <ExtLink href="https://www.python.org">Python</ExtLink> 3.10 or later and{" "}
            <ExtLink href="https://nodejs.org">Node.js</ExtLink> 18 or later.
          </li>
          <li>
            An internet connection the first time. The converter downloads Mojang's Bedrock samples
            (several hundred MB, once) and, for each Java version, the Java game files your pack builds
            on. They come from Mojang and are checked against Mojang's checksums.
          </li>
          <li>Free disk space: a few GB for a 256x pack.</li>
        </ul>
        <LinkButton href={LATEST}>Get the converter ZIP</LinkButton>
      </Section>

      <Section id="convert" title="Convert">
        <ol className="list-decimal space-y-3 pl-6">
          <li>Unzip the converter.</li>
          <li>
            Drag your Java pack ZIP onto <Code>Convert-Java-Pack.cmd</Code>.
            <ul className="mt-2 list-disc space-y-1 pl-6">
              <li>Several ZIPs: drop them together, base pack first. Later ones win.</li>
              <li>
                Your own Bedrock <Code>.mcpack</Code> can go in too. Its Vibrant Visuals lighting and
                fog are used. None of its art is.
              </li>
            </ul>
          </li>
          <li>Wait. A 256x pack takes a few minutes.</li>
          <li>
            Your pack is in <Code>converted/&lt;name&gt;/&lt;name&gt;.mcaddon</Code>.{" "}
            <Code>conversion.json</Code> next to it lists what converted, what did not and why.
          </li>
        </ol>
        <p>
          The launcher sets up Python, downloads Mojang's Bedrock samples once and accepts the author's
          Bedrock <Code>.mcpack</Code>.
        </p>
        <p>To test: install the engine and your <Code>.mcaddon</Code>, make a new world (or a copy), and turn on the engine and both parts of your pack. <Code>/scriptevent bct:control status</Code> shows what the engine is doing.</p>
      </Section>

      <Section id="what" title="What converts">
        <PairTable label="What converts from Java to Bedrock" head={["Your Java pack", "On Bedrock"]} rows={CONVERTS} />
        <p>One add-on serves Classic, Vibrant Visuals and ray tracing. It declares <Code>pbr</Code> and <Code>raytraced</Code>, so there is no setting to pick a mode.</p>
      </Section>

      <Section id="credits" title="Your name, credits and version">
        <p>A converted pack is still your pack.</p>
        <ul className="list-disc space-y-2 pl-6">
          <li><strong>Name:</strong> the ZIP's file name, with its version and resolution (such as <Code>My Pack R4.1.0 256x</Code>), or <Code>--title</Code>.</li>
          <li><strong>Description:</strong> your <Code>pack.mcmeta</Code> description, unchanged.</li>
          <li><strong>Credits:</strong> "By ..." lines in your description become the pack's authors, or pass <Code>--author</Code>.</li>
          <li><strong>Version:</strong> a version tag in the file name (<Code>R4.1.0</Code>, <Code>v4.1</Code>), or <Code>--version 4.1.0</Code>. Otherwise 1.0.0. Bedrock only replaces an installed pack when the version goes up, so to test the same version again, delete the old one (Settings, then Storage) first.</li>
          <li><strong>Java version:</strong> read from your <Code>pack.mcmeta</Code>. A pack made for 26.2 is filled in from 26.2's game files, one made for 26.3 from 26.3's.</li>
        </ul>
      </Section>

      <Section id="options" title="Options">
        <p>
          Pass options after the ZIP when you run the converter from a terminal. The full command line
          and every output file are in the <a className="text-accent underline underline-offset-4" href="#/docs/converter">converter guide</a>.
        </p>
        <PairTable label="Converter options" head={["Option", "What it does"]} rows={OPTIONS} mono />
        <CodeBlock label="Example command" language="bash">{`python converter/convert_java_author_pack.py --java "My Java Pack.zip" --output "converted/my-pack" --key my-pack --title "My Pack" --version 4.1.0`}</CodeBlock>
      </Section>

      <Section id="not-yet" title="What does not convert yet">
        <ul className="list-disc space-y-3 pl-6">
          {NOT_YET.map((t) => (
            <li key={t}>{t}</li>
          ))}
        </ul>
        <p><Code>conversion.json</Code> gives the exact list for your pack.</p>
        <Alert status="warning" className="max-w-3xl">
          <Alert.Indicator />
          <Alert.Content>
            <Alert.Title>Very large packs can fail to load</Alert.Title>
            <Alert.Description>
              12,000+ textures at 256x crashed the game in testing. <Code>conversion.json</Code>, under{" "}
              <Code>texture_load</Code>, shows the estimated texture memory per graphics mode and warns
              when a pack is in that range. Convert a lower-resolution edition if it warns.
            </Alert.Description>
          </Alert.Content>
        </Alert>
      </Section>

      <Section id="licence" title="Licensing and sharing">
        <ul className="list-disc space-y-2 pl-6">
          <li>
            The engine and the converter are GPL-3.0. The files the converter writes into your pack may
            be distributed under any terms you like, closed-source or paid. This is an additional
            permission, written in{" "}
            <ExtLink href={`${REPO}/blob/main/LICENSE-EXCEPTION.md`}>LICENSE-EXCEPTION.md</ExtLink>.
          </li>
          <li>
            The licence gives no rights in the pack you convert. Its art, models and sounds belong to
            its author.
          </li>
          <li>
            Converting a pack you are allowed to use, for your own play, is fine. Only the pack's
            author, or someone with the author's permission, should share or sell a converted pack.
          </li>
        </ul>
      </Section>
    </>
  );
}
