import { Card, Chip } from "@heroui/react";
import {
  Boxes,
  Dices,
  Layers,
  Leaf,
  Link2,
  Sparkles,
  Swords,
  Wrench,
  Grid3x3,
  Cat,
  type LucideIcon,
} from "lucide-react";
import { LinkButton, ExtLink } from "../components/ui";
import { LATEST, RELEASES, VERSION } from "../site";

/** Decorative: a patch of blocks where touching blocks join without seams. */
const ICON = `${import.meta.env.BASE_URL}pack-icon.png`;

const FEATURES: { icon: LucideIcon; title: string; text: string }[] = [
  { icon: Link2, title: "Connected textures", text: "Glass, bricks and other blocks join into one surface, using the pack's own ctm, horizontal, vertical and top rules." },
  { icon: Grid3x3, title: "Repeat patterns", text: "Big mosaics that span many blocks and keep their place in the pattern, on full blocks, slabs, stairs, fences and walls." },
  { icon: Dices, title: "Random textures", text: "Weighted random tiles, picked per position the way Java picks them." },
  { icon: Layers, title: "Overlays and edges", text: "Grass and sand spreading over other blocks, fallen leaves and similar decals, drawn as thin see-through blocks." },
  { icon: Leaf, title: "3D leaves", text: "Leaves drawn with the pack's own models, biome tinted, with vanilla decay and drops." },
  { icon: Swords, title: "3D held items", text: "Items held in hand with the pack's display poses, with inventory icons drawn from the model." },
  { icon: Cat, title: "Custom mob models", text: "Mob textures and EMF models with animations, with PBR in Vibrant Visuals." },
  { icon: Sparkles, title: "PBR materials", text: "LabPBR normal and specular maps become Bedrock normal and MER maps." },
  { icon: Boxes, title: "One pack, three modes", text: "Classic, Vibrant Visuals and ray tracing from the same pack. There is no setting to pick." },
];

const STEPS = [
  { n: 1, title: "Install the engine", text: "The engine is one Bedrock add-on. Players install it once." },
  { n: 2, title: "Install a converted pack", text: "A pack maker runs a Java texture pack through the converter. The result is one add-on that works with the engine." },
  { n: 3, title: "Play", text: "Blocks take the pack's look as they load, nearest first. Blocks you place change at once." },
];

export default function Home() {
  return (
    <>
      <section className="grid items-center gap-10 pb-16 pt-4 md:grid-cols-[1.2fr_1fr]">
        <div>
          <Chip color="accent" variant="soft" className="mb-4">
            <Chip.Label>Version {VERSION} · Bedrock 26.50 or newer</Chip.Label>
          </Chip>
          <h1 className="text-4xl font-bold tracking-tight sm:text-5xl">
            Java texture pack features on Minecraft Bedrock
          </h1>
          <p className="mt-5 max-w-xl text-lg leading-8 text-muted">
            Connected textures, big repeating patterns, random textures, overlays, 3D leaves and
            plants, 3D held items, custom mob models and PBR materials. In Classic, Vibrant Visuals
            and ray tracing, from one pack.
          </p>
          <div className="mt-8 flex flex-wrap gap-3">
            <LinkButton href={LATEST}>Download the engine</LinkButton>
            <LinkButton to="/players" variant="secondary">
              Install guide
            </LinkButton>
            <LinkButton to="/pack-makers" variant="outline">
              Convert a pack
            </LinkButton>
          </div>
        </div>
        <div className="flex justify-center md:justify-end">
          <img src={ICON} alt="" width={256} height={256}
            className="w-full max-w-sm rounded-3xl shadow-2xl ring-1 ring-border" />
        </div>
      </section>

      <section className="mb-16" aria-labelledby="what">
        <h2 id="what" className="text-2xl font-semibold tracking-tight">
          What it is
        </h2>
        <p className="mt-3 max-w-3xl leading-7">
          Bedrock Connected Textures (BCT) has three parts. The <strong>engine</strong> is a Bedrock
          add-on that draws what Bedrock cannot on its own. The <strong>converter</strong> is a tool
          for pack makers. It turns a Java texture pack into one Bedrock add-on that works with the
          engine. <strong>bct.py</strong> gives authors of Bedrock packs the same features in their own
          packs, written in plain Bedrock files. The tools run on your own computer. No account, upload
          or AI service is involved.
        </p>
      </section>

      <section className="mb-16" aria-labelledby="features">
        <h2 id="features" className="mb-6 text-2xl font-semibold tracking-tight">
          Features
        </h2>
        <ul className="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
          {FEATURES.map(({ icon: Icon, title, text }) => (
            <li key={title}>
              <Card className="h-full border border-border">
                <Card.Header>
                  <Icon className="mb-2 text-accent" size={26} aria-hidden="true" />
                  <Card.Title>{title}</Card.Title>
                </Card.Header>
                <Card.Content>
                  <p className="text-sm leading-6 text-muted">{text}</p>
                </Card.Content>
              </Card>
            </li>
          ))}
        </ul>
      </section>

      <section className="mb-16" aria-labelledby="how">
        <h2 id="how" className="mb-6 text-2xl font-semibold tracking-tight">
          How it works
        </h2>
        <ol className="grid gap-4 md:grid-cols-3">
          {STEPS.map((s) => (
            <li key={s.n}>
              <Card className="h-full border border-border">
                <Card.Header>
                  <span className="mb-2 inline-flex h-9 w-9 items-center justify-center rounded-full bg-accent font-semibold text-accent-foreground" aria-hidden="true">
                    {s.n}
                  </span>
                  <Card.Title>
                    <span className="sr-only">Step {s.n}: </span>
                    {s.title}
                  </Card.Title>
                </Card.Header>
                <Card.Content>
                  <p className="leading-6 text-muted">{s.text}</p>
                </Card.Content>
              </Card>
            </li>
          ))}
        </ol>
        <p className="mt-6 max-w-3xl leading-7">
          The engine swaps blocks for look-alike copies that can show the pack's patterns. They work
          like the normal blocks: mining, drops, tools, explosions and redstone behave the same.
          One command puts the world back to vanilla blocks before you take a pack out.
          Details are on the <a className="text-accent underline underline-offset-4" href="#/players">players page</a>.
        </p>
      </section>

      <section className="mb-16" aria-labelledby="get">
        <h2 id="get" className="mb-6 text-2xl font-semibold tracking-tight">
          Get it
        </h2>
        <Card className="border border-border">
          <Card.Content className="flex flex-col gap-6 md:flex-row md:items-center md:justify-between">
            <div className="space-y-2 leading-7">
              <p>
                <strong>Supported game version:</strong> Minecraft Bedrock 26.50 or newer. Older
                versions are not supported.
              </p>
              <p className="text-muted">
                Each release has the engine add-on (<code>.mcaddon</code>) and the converter ZIP.
                See all versions on the <ExtLink href={RELEASES}>releases page</ExtLink>.
              </p>
            </div>
            <div className="flex flex-wrap gap-3">
              <LinkButton href={LATEST}>Latest release</LinkButton>
              <LinkButton to="/docs/changelog" variant="secondary">
                Changelog
              </LinkButton>
            </div>
          </Card.Content>
        </Card>
      </section>

      <section aria-labelledby="who" className="mb-4">
        <h2 id="who" className="mb-6 text-2xl font-semibold tracking-tight">
          Where to go next
        </h2>
        <div className="grid gap-4 md:grid-cols-3">
          {[
            { to: "/players", t: "I want to play with a pack", d: "Install the engine and a converted pack, and see what the engine is doing.", icon: Swords },
            { to: "/pack-makers", t: "I made a Java pack", d: "Convert it with a drag and drop launcher. See what converts and what does not.", icon: Wrench },
            { to: "/developers", t: "I write Bedrock packs", d: "Patterns, 3D leaves, edges, overlays and connected textures in your own Bedrock pack.", icon: Boxes },
          ].map(({ to, t, d, icon: Icon }) => (
            <a key={to} href={`#${to}`} className="block rounded-2xl outline-none focus-visible:ring-2 focus-visible:ring-accent">
              <Card className="h-full border border-border transition-colors hover:bg-default">
                <Card.Header>
                  <Icon className="mb-2 text-accent" size={24} aria-hidden="true" />
                  <Card.Title>{t}</Card.Title>
                  <Card.Description>{d}</Card.Description>
                </Card.Header>
              </Card>
            </a>
          ))}
        </div>
      </section>
    </>
  );
}
