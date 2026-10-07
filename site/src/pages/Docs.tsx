import { Navigate, NavLink, useParams } from "react-router-dom";
import { Breadcrumbs, Card } from "@heroui/react";
import { DOCS } from "../docs";
import { Markdown, headings } from "../components/Markdown";

const linkClass = ({ isActive }: { isActive: boolean }) =>
  `block rounded-lg px-3 py-2 text-sm font-medium hover:bg-default ${
    isActive ? "bg-default text-foreground" : "text-muted"
  }`;

export default function Docs() {
  const { slug } = useParams();
  if (!slug) return <Navigate to="/docs/engine" replace />;
  const doc = DOCS[slug];
  if (!doc) return <Navigate to="/docs/engine" replace />;
  const toc = headings(doc.source);

  return (
    <div className="grid gap-8 lg:grid-cols-[16rem_minmax(0,1fr)]">
      <aside className="lg:sticky lg:top-24 lg:self-start">
        <nav aria-label="Documentation">
          <p className="mb-2 px-3 text-xs font-semibold uppercase tracking-wide text-muted">Docs</p>
          <ul className="flex flex-wrap gap-1 lg:flex-col">
            {Object.entries(DOCS).map(([key, d]) => (
              <li key={key}>
                <NavLink to={`/docs/${key}`} className={linkClass}>
                  {d.title}
                </NavLink>
              </li>
            ))}
          </ul>
        </nav>
        {toc.length > 0 && (
          <Card className="mt-6 hidden border border-border lg:block">
            <Card.Header>
              <Card.Title className="text-sm">On this page</Card.Title>
            </Card.Header>
            <Card.Content>
              <nav aria-label="On this page" className="max-h-[50vh] overflow-y-auto">
                <ul className="space-y-1 text-sm">
                  {toc.map((h) => (
                    <li key={h.id}>
                      <a
                        href={`#${h.id}`}
                        className="block py-0.5 text-muted hover:text-foreground"
                        onClick={(e) => {
                          e.preventDefault();
                          document.getElementById(h.id)?.scrollIntoView();
                        }}
                      >
                        {h.text}
                      </a>
                    </li>
                  ))}
                </ul>
              </nav>
            </Card.Content>
          </Card>
        )}
      </aside>
      <article className="min-w-0">
        <Breadcrumbs className="mb-4">
          <Breadcrumbs.Item href="#/docs/engine">Docs</Breadcrumbs.Item>
          <Breadcrumbs.Item>{doc.title}</Breadcrumbs.Item>
        </Breadcrumbs>
        <p className="mb-6 text-muted">{doc.blurb}</p>
        <Markdown source={doc.source} base={doc.base} />
      </article>
    </div>
  );
}
