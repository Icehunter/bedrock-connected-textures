import { useCallback, useEffect, useState } from "react";

export type Theme = "light" | "dark";

function current(): Theme {
  return document.documentElement.classList.contains("dark") ? "dark" : "light";
}

function apply(t: Theme, store: boolean, set: (t: Theme) => void) {
  const r = document.documentElement;
  r.classList.remove("light", "dark");
  r.classList.add(t);
  r.setAttribute("data-theme", t);
  if (store) {
    try {
      localStorage.setItem("bct-theme", t);
    } catch {
      /* storage can be blocked */
    }
  }
  set(t);
}

export function useTheme(): [Theme, () => void] {
  const [theme, setTheme] = useState<Theme>(current);

  useEffect(() => {
    const mq = window.matchMedia("(prefers-color-scheme: dark)");
    const onChange = (e: MediaQueryListEvent) => {
      let stored: string | null = null;
      try {
        stored = localStorage.getItem("bct-theme");
      } catch {
        /* storage can be blocked */
      }
      if (!stored) apply(e.matches ? "dark" : "light", false, setTheme);
    };
    mq.addEventListener("change", onChange);
    return () => mq.removeEventListener("change", onChange);
  }, []);

  const toggle = useCallback(() => {
    apply(current() === "dark" ? "light" : "dark", true, setTheme);
  }, []);

  return [theme, toggle];
}
