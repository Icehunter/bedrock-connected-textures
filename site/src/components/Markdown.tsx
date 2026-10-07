import { isValidElement, type ReactNode } from "react";
import ReactMarkdown, { type Components } from "react-markdown";
import remarkGfm from "remark-gfm";
import { Link as RouterLink } from "react-router-dom";
import { REPO } from "../site";
import { Code } from "./Code";

export function slugify(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^\w\s-]/g, "")
    .trim()
    .replace(/\s+/g, "-");
}

function textOf(node: ReactNode): string {
  if (typeof node === "string" || typeof node === "number") return String(node);
  if (Array.isArray(node)) return node.map(textOf).join("");
  if (isValidElement(node)) return textOf((node.props as { children?: ReactNode }).children);
  return "";
}

const DOC_ROUTES: Record<string, string> = {
  "ENGINE.md": "/docs/engine",
  "CONVERTER.md": "/docs/converter",
  "PACK-MAKERS.md": "/pack-makers",
  "AUTHORING.md": "/docs/authoring",
};

/** A link's path in the repository, from the folder of the document it is in ("docs/" or ""). */
function repoPath(href: string, base: string): string {
  const parts = (base + href.replace(/^\.\//, "")).split("/");
  const out: string[] = [];
  for (const part of parts) {
    if (part === "..") out.pop();
    else if (part) out.push(part);
  }
  return out.join("/");
}

function scrollToId(id: string) {
  const el = document.getElementById(id);
  if (el) {
    el.scrollIntoView({ block: "start" });
    el.setAttribute("tabindex", "-1");
    el.focus({ preventScroll: true });
  }
}

const components = (base: string): Components => ({
  h1: ({ children }) => <h1 id={slugify(textOf(children))}>{children}</h1>,
  h2: ({ children }) => <h2 id={slugify(textOf(children))}>{children}</h2>,
  h3: ({ children }) => <h3 id={slugify(textOf(children))}>{children}</h3>,
  table: ({ children }) => (
    <div className="table-wrap" role="region" aria-label="Table" tabIndex={0}>
      <table>{children}</table>
    </div>
  ),
  // Fenced code: the <code> inside carries the fence's language as language-<name>.
  pre: ({ children }) => {
    const inner = Array.isArray(children) ? children[0] : children;
    const className = isValidElement(inner) ? String((inner.props as { className?: string }).className ?? "") : "";
    return <Code code={textOf(children)} language={/language-(\S+)/.exec(className)?.[1]} />;
  },
  a: ({ href = "", children }) => {
    if (href.startsWith("#")) {
      return (
        <a
          href={href}
          onClick={(e) => {
            e.preventDefault();
            scrollToId(href.slice(1));
          }}
        >
          {children}
        </a>
      );
    }
    if (/^https?:/.test(href)) {
      return (
        <a href={href} target="_blank" rel="noopener noreferrer">
          {children}
        </a>
      );
    }
    const file = href.split("/").pop() ?? "";
    const route = DOC_ROUTES[file];
    if (route) return <RouterLink to={route}>{children}</RouterLink>;
    return (
      <a href={`${REPO}/blob/main/${repoPath(href, base)}`} target="_blank" rel="noopener noreferrer">
        {children}
      </a>
    );
  },
});

/** A markdown document; base is the repository folder it lives in, for its relative links. */
export function Markdown({ source, base = "docs/" }: { source: string; base?: string }) {
  return (
    <div className="doc">
      <ReactMarkdown remarkPlugins={[remarkGfm]} components={components(base)}>
        {source}
      </ReactMarkdown>
    </div>
  );
}

/** Level-2 headings of a markdown document, for a table of contents. */
export function headings(source: string): { id: string; text: string }[] {
  const out: { id: string; text: string }[] = [];
  let fence = false;
  for (const line of source.split("\n")) {
    if (line.startsWith("```")) fence = !fence;
    if (fence) continue;
    const m = /^## (.+)$/.exec(line);
    if (m) {
      const text = m[1].replace(/`/g, "");
      out.push({ id: slugify(text), text });
    }
  }
  return out;
}
