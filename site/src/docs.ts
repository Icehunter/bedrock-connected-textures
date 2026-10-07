/** The repository docs the site shows, by route slug. */
import engine from "../../docs/ENGINE.md?raw";
import converter from "../../docs/CONVERTER.md?raw";
import authoring from "../../docs/AUTHORING.md?raw";
import changelog from "../../CHANGELOG.md?raw";

export const DOCS: Record<string, { title: string; blurb: string; source: string; base?: string }> = {
  authoring: { title: "Bedrock pack guide", blurb: "Patterns, leaves, edges, overlays and connected textures in your own Bedrock pack.", source: authoring },
  engine: { title: "Engine", blurb: "How the engine draws, swaps and limits.", source: engine },
  converter: { title: "Converter guide", blurb: "Options, outputs and checks.", source: converter },
  changelog: { title: "Changelog", blurb: "What changed in each release.", source: changelog, base: "" },
};
