import { useEffect } from "react";
import { NavLink, Outlet, useLocation } from "react-router-dom";
import { Button, Drawer, Separator, useOverlayState } from "@heroui/react";
import { Menu, Moon, Sun, X } from "lucide-react";
import { useTheme } from "../theme";
import { ISSUES, LATEST, NAV, REPO, VERSION } from "../site";
import { ExtLink } from "./ui";
import { Search } from "./Search";

function GithubMark() {
  return (
    <svg width="20" height="20" viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">
      <path d="M8 0C3.58 0 0 3.58 0 8c0 3.54 2.29 6.53 5.47 7.59.4.07.55-.17.55-.38 0-.19-.01-.82-.01-1.49-2.01.37-2.53-.49-2.69-.94-.09-.23-.48-.94-.82-1.13-.28-.15-.68-.52-.01-.53.63-.01 1.08.58 1.23.82.72 1.21 1.87.87 2.33.66.07-.52.28-.87.51-1.07-1.78-.2-3.64-.89-3.64-3.95 0-.87.31-1.59.82-2.15-.08-.2-.36-1.02.08-2.12 0 0 .67-.21 2.2.82a7.6 7.6 0 0 1 4 0c1.53-1.04 2.2-.82 2.2-.82.44 1.1.16 1.92.08 2.12.51.56.82 1.27.82 2.15 0 3.07-1.87 3.75-3.65 3.95.29.25.54.73.54 1.48 0 1.07-.01 1.93-.01 2.2 0 .21.15.46.55.38A8.01 8.01 0 0 0 16 8c0-4.42-3.58-8-8-8z" />
    </svg>
  );
}

function Logo() {
  return (
    <span className="flex items-center gap-2 font-semibold">
      <img src={`${import.meta.env.BASE_URL}pack-icon.png`} alt="" width={28} height={28} className="rounded-md" />
      <span>Bedrock Connected Textures</span>
    </span>
  );
}

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `rounded-lg px-3 py-2 text-sm font-medium transition-colors hover:bg-default ${
    isActive ? "bg-default text-foreground" : "text-muted"
  }`;

function ThemeButton() {
  const [theme, toggle] = useTheme();
  const next = theme === "dark" ? "light" : "dark";
  return (
    <Button isIconOnly variant="ghost" onPress={toggle} aria-label={`Switch to ${next} mode`}>
      {theme === "dark" ? <Sun size={18} aria-hidden="true" /> : <Moon size={18} aria-hidden="true" />}
    </Button>
  );
}

function MobileMenu() {
  const state = useOverlayState();
  const { pathname } = useLocation();
  useEffect(() => {
    state.close();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [pathname]);
  return (
    <Drawer state={state}>
      <Button isIconOnly variant="ghost" className="md:hidden" onPress={state.open} aria-label="Open menu">
        <Menu size={20} aria-hidden="true" />
      </Button>
      <Drawer.Backdrop>
        <Drawer.Content placement="right">
          <Drawer.Dialog aria-label="Site menu">
            <Drawer.Header>
              <Drawer.Heading>Menu</Drawer.Heading>
              <Button isIconOnly variant="ghost" onPress={state.close} aria-label="Close menu">
                <X size={18} aria-hidden="true" />
              </Button>
            </Drawer.Header>
            <Drawer.Body>
              <nav aria-label="Main" className="flex flex-col gap-1">
                {NAV.map((n) => (
                  <NavLink key={n.to} to={n.to} end={n.end} className={linkClass}>
                    {n.label}
                  </NavLink>
                ))}
                <Separator className="my-2" />
                <a className="rounded-lg px-3 py-2 text-sm font-medium text-muted hover:bg-default" href={REPO} target="_blank" rel="noopener noreferrer">
                  GitHub repository
                </a>
              </nav>
            </Drawer.Body>
          </Drawer.Dialog>
        </Drawer.Content>
      </Drawer.Backdrop>
    </Drawer>
  );
}

export default function Layout() {
  const { pathname } = useLocation();
  useEffect(() => {
    window.scrollTo(0, 0);
    document.getElementById("main")?.focus({ preventScroll: true });
  }, [pathname]);

  return (
    <div className="flex min-h-screen flex-col">
      <a href="#main" className="skip-link" onClick={(e) => { e.preventDefault(); document.getElementById("main")?.focus(); }}>
        Skip to content
      </a>
      <header className="sticky top-0 z-40 border-b border-border bg-background/85 backdrop-blur">
        <div className="mx-auto flex h-16 max-w-6xl items-center justify-between gap-4 px-4">
          <NavLink to="/" aria-label="Bedrock Connected Textures, home">
            <Logo />
          </NavLink>
          <nav aria-label="Main" className="hidden items-center gap-1 md:flex">
            {NAV.map((n) => (
              <NavLink key={n.to} to={n.to} end={n.end} className={linkClass}>
                {n.label}
              </NavLink>
            ))}
          </nav>
          <div className="flex items-center gap-1">
            <Search />
            <a
              href={REPO}
              target="_blank"
              rel="noopener noreferrer"
              aria-label="GitHub repository (opens in a new tab)"
              className="hidden h-10 w-10 items-center justify-center rounded-xl text-foreground hover:bg-default sm:inline-flex"
            >
              <GithubMark />
            </a>
            <ThemeButton />
            <MobileMenu />
          </div>
        </div>
      </header>

      <main id="main" tabIndex={-1} className="mx-auto w-full max-w-6xl flex-1 px-4 py-10 outline-none">
        <Outlet />
      </main>

      <footer className="border-t border-border">
        <div className="mx-auto grid max-w-6xl gap-6 px-4 py-10 text-sm text-muted md:grid-cols-[2fr_1fr]">
          <div className="space-y-2">
            <p className="font-semibold text-foreground">Bedrock Connected Textures {VERSION}</p>
            <p>
              The engine and the converter are GPL-3.0, with one additional permission: everything the
              converter writes into a converted pack may be distributed under any terms, closed-source
              or paid. Changes to the engine or the converter stay under the GPL.
            </p>
            <p>
              The licence covers this program only. It gives no rights in the pack you convert. Only
              the pack's author, or someone with the author's permission, should share a converted pack.
            </p>
            <p>Not affiliated with Mojang or Microsoft. This site sets no cookies and loads no trackers.</p>
          </div>
          <nav aria-label="Project links" className="flex flex-col gap-2">
            <ExtLink href={REPO}>Source on GitHub</ExtLink>
            <ExtLink href={LATEST}>Releases</ExtLink>
            <ExtLink href={ISSUES}>Issues</ExtLink>
            <ExtLink href={`${REPO}/blob/main/LICENSE`}>GPL-3.0 licence</ExtLink>
            <ExtLink href={`${REPO}/blob/main/LICENSE-EXCEPTION.md`}>Converter output exception</ExtLink>
          </nav>
        </div>
      </footer>
    </div>
  );
}
