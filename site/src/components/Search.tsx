import { useEffect, useMemo, useState, type ReactNode } from "react";
import { useNavigate } from "react-router-dom";
import { Button, Kbd } from "@heroui/react";
import { Command } from "@heroui-pro/react";
import { BookOpen, FileText, Hash, Search as SearchIcon } from "lucide-react";
import { DOCS } from "../docs";
import { slugify } from "./Markdown";

const PAGES = [
  { path: "/", title: "Home" },
  { path: "/players", title: "Players: install and use" },
  { path: "/pack-makers", title: "Pack makers: convert a Java pack" },
  { path: "/developers", title: "Bedrock pack authors and developers" },
];
// Results shown at most, and how much text a snippet shows on each side of the match.
const MAX_RESULTS = 40, SNIPPET = 70;

type Entry = {
  key: string; path: string; anchor?: string; title: string; where?: string;
  kind: "page" | "doc" | "section"; text: string;
};

/** Markdown as plain words: code and link text kept, the syntax around them dropped. */
function plain(markdown: string): string {
  return markdown
    .replace(/^```.*$/gm, " ")
    .replace(/!?\[([^\]]*)\]\([^)]*\)/g, "$1")
    .replace(/[`*_>|#]/g, " ")
    .replace(/^\s*[-+]\s+/gm, " ")
    .replace(/-{3,}/g, " ")
    .replace(/\s+/g, " ")
    .trim();
}

/** Every page, every doc (with the text before its first section) and every section of a doc with its text. */
function entries(): Entry[] {
  const list: Entry[] = PAGES.map((page) => ({ key: "page:" + page.path, path: page.path, title: page.title, kind: "page", text: "" }));
  for (const [slug, doc] of Object.entries(DOCS)) {
    const path = `/docs/${slug}`;
    const intro: string[] = [];
    let section: Entry | undefined, lines: string[] = [], fence = false;
    const close = () => { if (section) { section.text = plain(lines.join("\n")); list.push(section); } lines = []; };
    for (const line of doc.source.split("\n")) {
      if (line.startsWith("```")) fence = !fence;
      const heading = fence ? null : /^(##|###) (.+)$/.exec(line);
      if (heading) {
        close();
        const title = heading[2].replace(/`/g, "");
        section = { key: `section:${slug}:${slugify(title)}`, path, anchor: slugify(title), title, where: doc.title, kind: "section", text: "" };
      } else if (section) lines.push(line);
      else intro.push(line);
    }
    close();
    list.push({ key: "doc:" + slug, path, title: doc.title, where: doc.blurb, kind: "doc", text: plain(intro.join("\n")) });
  }
  return list;
}

/** The words of a query, lower case. */
const words = (query: string) => query.toLowerCase().split(/\s+/).filter(Boolean);

/** Entries that hold every word of the query, best first: the whole query in the title, every word in the title, then the text. */
function search(all: Entry[], query: string): Entry[] {
  const terms = words(query), phrase = query.trim().toLowerCase();
  if (!terms.length) return all.filter((entry) => entry.kind !== "section");
  const scored: [number, number, Entry][] = [];
  all.forEach((entry, order) => {
    const title = `${entry.title} ${entry.where ?? ""}`.toLowerCase(), text = entry.text.toLowerCase();
    if (!terms.every((term) => title.includes(term) || text.includes(term))) return;
    const score = title.includes(phrase) ? 3 : terms.every((term) => title.includes(term)) ? 2 : text.includes(phrase) ? 1 : 0;
    scored.push([score, order, entry]);
  });
  return scored.sort((a, b) => b[0] - a[0] || a[1] - b[1]).slice(0, MAX_RESULTS).map(([, , entry]) => entry);
}

/** The text around the first match of the query, with every query word marked. */
function snippet(text: string, query: string): ReactNode {
  const terms = words(query), lower = text.toLowerCase(), phrase = query.trim().toLowerCase();
  let at = lower.indexOf(phrase);
  if (at < 0) at = Math.min(...terms.map((term) => lower.indexOf(term)).filter((index) => index >= 0));
  if (!Number.isFinite(at) || at < 0) return null;
  const start = Math.max(0, at - SNIPPET), end = Math.min(text.length, at + phrase.length + SNIPPET);
  const part = text.slice(start, end);
  const pattern = new RegExp(`(${terms.map((term) => term.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("|")})`, "gi");
  return (
    <>
      {start > 0 && "... "}
      {part.split(pattern).map((piece, index) =>
        index % 2 ? <mark key={index} className="rounded-sm bg-accent/25 text-foreground">{piece}</mark> : piece)}
      {end < text.length && " ..."}
    </>
  );
}

const ICONS = { page: FileText, doc: BookOpen, section: Hash };
const GROUPS: [string, Entry["kind"]][] = [["Pages", "page"], ["Docs", "doc"], ["Sections", "section"]];

/** Whether the visitor is on a Mac, where the shortcut is Command+K rather than Ctrl+K. */
function onMac(): boolean {
  const platform = (navigator as Navigator & { userAgentData?: { platform?: string } }).userAgentData?.platform ?? navigator.platform ?? "";
  return /mac|iphone|ipad/i.test(platform);
}

/** The header's search button and the palette it opens (also Ctrl/Cmd+K): pages, docs and the full text of every doc section. */
export function Search() {
  const [isOpen, setOpen] = useState(false);
  const [query, setQuery] = useState("");
  const navigate = useNavigate();
  const all = useMemo(entries, []);
  const byKey = useMemo(() => new Map(all.map((entry) => [entry.key, entry])), [all]);
  const results = useMemo(() => search(all, query), [all, query]);
  const mac = useMemo(onMac, []);

  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        setOpen((open) => !open);
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  function close(open: boolean) {
    setOpen(open);
    if (!open) setQuery("");
  }

  function go(key: string) {
    const entry = byKey.get(key);
    if (!entry) return;
    close(false);
    navigate(entry.path);
    // The layout scrolls to the top on a new page; the section is scrolled to after that.
    if (entry.anchor) setTimeout(() => document.getElementById(entry.anchor!)?.scrollIntoView({ block: "start" }), 80);
  }

  return (
    <>
      <Button variant="ghost" onPress={() => setOpen(true)} aria-label="Search the site and docs" className="gap-2">
        <SearchIcon size={16} aria-hidden="true" />
        <span className="hidden text-sm text-muted lg:inline">Search</span>
        <Kbd className="hidden text-xs lg:inline-flex">
          {mac ? <Kbd.Abbr keyValue="command" /> : <Kbd.Content>Ctrl</Kbd.Content>}
          <Kbd.Content>K</Kbd.Content>
        </Kbd>
      </Button>
      <Command>
        <Command.Backdrop isOpen={isOpen} onOpenChange={close} variant="blur">
          <Command.Container size="lg">
            {/* The results are found here, across the full text; the palette's own filter lets them all through. */}
            <Command.Dialog aria-label="Search" inputValue={query} onInputChange={setQuery} filter={() => true}>
              <Command.InputGroup>
                <Command.InputGroup.Prefix>
                  <SearchIcon size={16} aria-hidden="true" />
                </Command.InputGroup.Prefix>
                <Command.InputGroup.Input placeholder="Search the docs: a setting, a command, a block..." />
                <Command.InputGroup.ClearButton />
              </Command.InputGroup>
              <Command.List
                aria-label="Results"
                onAction={(key) => go(String(key))}
                renderEmptyState={() => <div className="flex h-12 items-center justify-center text-sm text-muted">No results.</div>}
              >
                {GROUPS.filter(([, kind]) => results.some((entry) => entry.kind === kind)).map(([heading, kind]) => (
                  <Command.Group key={kind} heading={heading}>
                    {results.filter((entry) => entry.kind === kind).map((entry) => {
                      const Icon = ICONS[entry.kind];
                      const shown = query.trim() ? snippet(entry.text, query) : null;
                      return (
                        <Command.Item key={entry.key} id={entry.key} textValue={entry.title}>
                          <Icon size={16} aria-hidden="true" className="mt-0.5 shrink-0 self-start" />
                          <div className="min-w-0 flex-1">
                            <div className="flex items-baseline gap-3">
                              <span className="truncate">{entry.title}</span>
                              {entry.where && <span className="ml-auto shrink-0 truncate text-xs text-muted">{entry.where}</span>}
                            </div>
                            {shown && <p className="mt-0.5 line-clamp-2 text-xs leading-5 text-muted">{shown}</p>}
                          </div>
                        </Command.Item>
                      );
                    })}
                  </Command.Group>
                ))}
              </Command.List>
            </Command.Dialog>
          </Command.Container>
        </Command.Backdrop>
      </Command>
    </>
  );
}
