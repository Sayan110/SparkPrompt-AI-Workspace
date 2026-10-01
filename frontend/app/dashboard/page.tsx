"use client";

import Link from "next/link";
import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Icon } from "@/components/icons";
import { CardSkeleton, EmptyState, ErrorState } from "@/components/states";
import { Badge, Button, Card, PageHeader, SectionHeader } from "@/components/ui";
import { api, type ApiProject, type ApiPrompt } from "@/lib/api";
import { roleNames, shortTitle } from "@/lib/prompt-engine";

function greeting(): string {
  const hour = new Date().getHours();
  if (hour < 12) return "Good morning";
  if (hour < 18) return "Good afternoon";
  return "Good evening";
}

function formatDate(): string {
  return new Date().toLocaleDateString(undefined, { weekday: "long", month: "long", day: "numeric" });
}

export default function DashboardPage() {
  const [prompts, setPrompts] = useState<ApiPrompt[] | null>(null);
  const [projects, setProjects] = useState<ApiProject[] | null>(null);
  const [error, setError] = useState("");

  async function load() {
    setError("");
    setPrompts(null);
    setProjects(null);
    try {
      const [promptList, projectList] = await Promise.all([api.listPrompts(), api.listProjects()]);
      setPrompts(promptList);
      setProjects(projectList);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not reach your workspace.");
      setPrompts([]);
      setProjects([]);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const recent = (prompts ?? []).slice(0, 4);

  return (
    <AppShell>
      <div className="mx-auto max-w-[1400px] px-6 pt-8 pb-16 sm:px-8">
        <PageHeader
          eyebrow={formatDate()}
          title={
            <>
              {greeting()}, welcome back. <em className="text-purple">Make every prompt brilliant.</em>
            </>
          }
          description="Start in Magic Studio, pick up a recent prompt, or organize work in a project."
          actions={
            <Button variant="primary" icon="plus" size="lg" onClick={() => (window.location.href = "/studio")}>
              Create prompt
            </Button>
          }
        />

        {/* Secondary actions */}
        <div className="mt-6 flex flex-wrap gap-2">
          <Button variant="secondary" icon="studio" onClick={() => (window.location.href = "/studio")}>
            Open Studio
          </Button>
          <Button variant="secondary" icon="library" onClick={() => (window.location.href = "/library")}>
            Browse Library
          </Button>
          <Button variant="secondary" icon="folder" onClick={() => (window.location.href = "/projects")}>
            New project
          </Button>
        </div>

        {error ? (
          <div className="mt-6">
            <ErrorState title="Workspace unavailable" message={error} retry={() => void load()} />
          </div>
        ) : null}

        <div className="mt-10 grid gap-8 lg:grid-cols-[1.6fr_1fr]">
          {/* Recent prompts */}
          <section aria-labelledby="recent-prompts">
            <div id="recent-prompts">
              <SectionHeader
                title="Recent prompts"
                action={
                  <Link href="/library" className="inline-flex items-center gap-1 text-xs font-semibold text-purple hover:underline">
                    View library <Icon name="chevron-right" size={13} />
                  </Link>
                }
              />
            </div>
            {prompts === null ? (
              <CardSkeleton count={2} />
            ) : recent.length ? (
              <div className="grid gap-2">
                {recent.map((prompt) => (
                  // Phase 3R (D6): deep-link straight into Studio — the same
                  // ?prompt=<id> attachment path Library uses, handled by 3Q.
                  <Link key={prompt.id} href={`/studio?prompt=${prompt.id}`} className="group block rounded-xl border border-border bg-card p-4 transition hover:border-[#c9c0e6] hover:shadow-[0_6px_18px_rgba(40,30,70,.07)]">
                    <div className="flex items-center gap-3">
                      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-purple-soft text-purple">
                        <Icon name="spark" size={16} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <strong className="block truncate text-[13px] font-bold text-ink group-hover:text-purple-dark dark:group-hover:text-[#d3c5ff]">
                          {prompt.title}
                        </strong>
                        <p className="mt-0.5 truncate text-xs text-muted">
                          {prompt.idea ? shortTitle(prompt.idea) : "Open to review"}
                        </p>
                      </div>
                      <Badge tone="purple">{roleNames[(prompt.audience as keyof typeof roleNames) ?? "everyone"] ?? prompt.audience}</Badge>
                      <span className="hidden shrink-0 text-[11px] text-faint sm:block">
                        {new Date(prompt.updated_at).toLocaleDateString(undefined, { month: "short", day: "numeric" })}
                      </span>
                    </div>
                  </Link>
                ))}
              </div>
            ) : (
              <Card className="p-0">
                <EmptyState
                  icon="studio"
                  title="No prompts yet"
                  body="Create your first prompt in Magic Studio — it takes a rough idea and turns it into a polished prompt."
                  action={
                    <Button variant="primary" icon="plus" onClick={() => (window.location.href = "/studio")}>
                      Create prompt
                    </Button>
                  }
                />
              </Card>
            )}
          </section>

          {/* Right rail: workspace activity */}
          <div className="grid gap-8">
            <section aria-labelledby="workspace-activity">
              <div id="workspace-activity">
                <SectionHeader title="Workspace" />
              </div>
              <Card className="p-5">
                <dl className="grid gap-4">
                  <div className="flex items-center justify-between">
                    <dt className="flex items-center gap-2 text-[13px] font-semibold text-muted">
                      <Icon name="spark" size={15} className="text-purple" /> Prompts saved
                    </dt>
                    <dd className="text-lg font-extrabold text-ink">{prompts === null ? "—" : prompts.length}</dd>
                  </div>
                  <div className="flex items-center justify-between">
                    <dt className="flex items-center gap-2 text-[13px] font-semibold text-muted">
                      <Icon name="folder" size={15} className="text-purple" /> Projects
                    </dt>
                    <dd className="text-lg font-extrabold text-ink">{projects === null ? "—" : projects.length}</dd>
                  </div>
                </dl>
                <p className="mt-4 border-t border-border pt-3 text-[11px] leading-5 text-faint">
                  Counts come from your local workspace database. AI provider support is built
                  into the gateway — which providers are configured depends on your environment
                  (see Settings).
                </p>
              </Card>
            </section>

            <section aria-labelledby="recent-projects">
              <div id="recent-projects">
                <SectionHeader
                  title="Projects"
                  action={
                    <Link href="/projects" className="inline-flex items-center gap-1 text-xs font-semibold text-purple hover:underline">
                      All projects <Icon name="chevron-right" size={13} />
                    </Link>
                  }
                />
              </div>
              {projects === null ? (
                <CardSkeleton count={1} />
              ) : projects.length ? (
                <div className="grid gap-2">
                  {projects.slice(0, 3).map((project) => (
                    <Link key={project.id} href="/projects" className="flex items-center gap-3 rounded-xl border border-border bg-card px-4 py-3 transition hover:border-[#c9c0e6]">
                      <span className="grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-purple-soft text-purple">
                        <Icon name="folder" size={15} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <strong className="block truncate text-[13px] font-bold text-ink">{project.name}</strong>
                        <p className="truncate text-[11px] text-muted">{project.description || "No description"}</p>
                      </div>
                      <Icon name="chevron-right" size={15} className="shrink-0 text-faint" />
                    </Link>
                  ))}
                </div>
              ) : (
                <Card className="p-5">
                  <p className="text-[13px] leading-6 text-muted">
                    No projects yet. Projects group prompts by body of work.
                  </p>
                  <Button variant="secondary" size="sm" icon="plus" className="mt-3" onClick={() => (window.location.href = "/projects")}>
                    New project
                  </Button>
                </Card>
              )}
            </section>
          </div>
        </div>
      </div>
    </AppShell>
  );
}