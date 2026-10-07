import { useSyncExternalStore } from "react";
import { CodeBlock } from "@heroui-pro/react/code-block";

/** Whether the page is in dark mode, following the theme class on <html>. */
function subscribe(onChange: () => void) {
  const observer = new MutationObserver(onChange);
  observer.observe(document.documentElement, { attributes: true, attributeFilter: ["class"] });
  return () => observer.disconnect();
}
const isDark = () => document.documentElement.classList.contains("dark");

// Fence names in the docs and the Shiki languages they map to; anything else shows as plain text.
const LANGUAGES: Record<string, string> = {
  js: "javascript", javascript: "javascript", json: "json", bash: "bash", sh: "bash", shell: "bash",
  powershell: "powershell", ts: "typescript", tsx: "tsx", yaml: "yaml", mcfunction: "mcfunction",
};

/** A highlighted code block with its label (or language) and a copy button. */
export function Code({ code, language = "", label }: { code: string; language?: string; label?: string }) {
  const dark = useSyncExternalStore(subscribe, isDark, () => false);
  const shiki = LANGUAGES[language.toLowerCase()] ?? "plaintext";
  const text = code.replace(/\n$/, "");
  return (
    <CodeBlock className="my-4" aria-label={label ?? (language || "code")}>
      <CodeBlock.Header>
        <span className="text-xs text-muted">{label ?? (language || "text")}</span>
        <CodeBlock.CopyButton code={text} />
      </CodeBlock.Header>
      <CodeBlock.Code code={text} language={shiki} theme={dark ? "github-dark" : "github-light"} />
    </CodeBlock>
  );
}
