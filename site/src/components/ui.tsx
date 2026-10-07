import type { ReactNode } from "react";
import { Link as RouterLink } from "react-router-dom";
import { Card, Table } from "@heroui/react";
import { buttonVariants } from "@heroui/styles";
import { ExternalLink } from "lucide-react";
import { Code as ProCode } from "./Code";

type Variant = "primary" | "secondary" | "tertiary" | "outline" | "ghost";

/** A link that looks like a HeroUI button. Internal paths use the router, others open a new tab. */
export function LinkButton({
  to,
  href,
  variant = "primary",
  size = "lg",
  children,
}: {
  to?: string;
  href?: string;
  variant?: Variant;
  size?: "sm" | "md" | "lg";
  children: ReactNode;
}) {
  const className = buttonVariants({ variant, size });
  if (to) {
    return (
      <RouterLink to={to} className={className}>
        {children}
      </RouterLink>
    );
  }
  return (
    <a href={href} className={className} target="_blank" rel="noopener noreferrer">
      {children}
      <ExternalLink size={16} aria-hidden="true" />
      <span className="sr-only"> (opens in a new tab)</span>
    </a>
  );
}

export function ExtLink({ href, children }: { href: string; children: ReactNode }) {
  return (
    <a
      href={href}
      target="_blank"
      rel="noopener noreferrer"
      className="text-accent underline underline-offset-4 hover:opacity-80"
    >
      {children}
    </a>
  );
}

export function PageHeader({ title, lead }: { title: string; lead: ReactNode }) {
  return (
    <header className="mb-10 max-w-3xl">
      <h1 className="text-3xl font-bold tracking-tight sm:text-4xl">{title}</h1>
      <p className="mt-3 text-lg text-muted">{lead}</p>
    </header>
  );
}

export function Section({
  id,
  title,
  children,
}: {
  id?: string;
  title: string;
  children: ReactNode;
}) {
  return (
    <section id={id} className="mb-14" aria-labelledby={id ? `${id}-h` : undefined}>
      <h2 id={id ? `${id}-h` : undefined} className="mb-4 text-2xl font-semibold tracking-tight">
        {title}
      </h2>
      <div className="space-y-4 leading-7">{children}</div>
    </section>
  );
}

export function Code({ children }: { children: ReactNode }) {
  return (
    <code className="rounded-md bg-default px-1.5 py-0.5 font-mono text-[0.875em]">{children}</code>
  );
}

export function CodeBlock({ children, label, language }: { children: string; label: string; language?: string }) {
  return <ProCode code={children} label={label} language={language} />;
}

export function Note({ title, children }: { title: string; children: ReactNode }) {
  return (
    <Card className="border border-border">
      <Card.Header>
        <Card.Title>{title}</Card.Title>
      </Card.Header>
      <Card.Content className="space-y-2 leading-7">{children}</Card.Content>
    </Card>
  );
}

/** A two-column HeroUI table. */
export function PairTable({
  label,
  head,
  rows,
  mono = false,
}: {
  label: string;
  head: [string, string];
  rows: [string, string][];
  mono?: boolean;
}) {
  return (
    <Table>
      <Table.ScrollContainer>
        <Table.Content aria-label={label} className="min-w-[36rem]">
          <Table.Header>
            <Table.Column isRowHeader>{head[0]}</Table.Column>
            <Table.Column>{head[1]}</Table.Column>
          </Table.Header>
          <Table.Body>
            {rows.map(([a, b]) => (
              <Table.Row key={a}>
                <Table.Cell className={mono ? "align-top font-mono text-sm" : "align-top font-medium"}>
                  {a}
                </Table.Cell>
                <Table.Cell className="align-top whitespace-normal">{b}</Table.Cell>
              </Table.Row>
            ))}
          </Table.Body>
        </Table.Content>
      </Table.ScrollContainer>
    </Table>
  );
}
