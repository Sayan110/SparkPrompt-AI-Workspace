"use client";

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { Icon } from "@/components/icons";
import { EmptyState, ErrorState, ListSkeleton } from "@/components/states";
import { Badge, Button, Card, ConfirmDialog, Input, PageHeader, SectionHeader } from "@/components/ui";
import { api, type ApiPrompt } from "@/lib/api";
import { recipes } from "@/lib/content";
import { roleNames } from "@/lib/prompt-engine";
import { useToast } from "@/lib/toast";
import { copyText } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace";

const AUDIENCE_SHORT: Record<string, string> = {
  everyone: "General",
  developer: "Developer",
  creator: "Creator",
  business: "Business",
  student: "Student",
};

export default function LibraryPage() {
  const router = useRouter();
  const { sparks, clearSparks } = useWorkspace();
  const { showToast } = useToast();
  const [savedPrompts, setSavedPrompts] = useState<ApiPrompt[] | null>(null);
  const [savedError, setSavedError] = useState("");
  const [query, setQuery] = useState("");
  const [deleting, setDeleting] = useState<ApiPrompt | null>(null);
  const [deletingBusy, setDeletingBusy] = useState(false);

  async function loadSaved() {
    setSavedError("");
    setSavedPrompts(null);
    try {
      setSavedPrompts(await api.listPrompts());
    } catch (error) {
      setSavedError(error instanceof Error ? error.message : "Could not load saved prompts.");
      setSavedPrompts([]);
    }
  }

  useEffect(() => {
    void loadSaved();
  }, []);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return savedPrompts ?? [];
    return (savedPrompts ?? []).filter(
      (item) =>
        item.title.toLowerCase().includes(needle) ||
        (item.idea ?? "").toLowerCase().includes(needle) ||
        (item.body ?? "").toLowerCase().includes(needle),
    );
  }, [savedPrompts, query]);

  async function confirmDelete() {
    if (!deleting) return;
    setDeletingBusy(true);
    try {
      await api.deletePrompt(deleting.id);
      showToast("Prompt deleted.");
      setDeleting(null);
      void loadSaved();
    } catch (error) {
      showToast(error instanceof Error ? error.message : "Could not delete the prompt.", "error");
    } finally {
      setDeletingBusy(false);
    }
  }

  async function copyPrompt(item: ApiPrompt) {
    let body = item.body ?? null;
    if (!body) {
      try {
        body = (await api.getPrompt(item.id)).body ?? null;
      } catch {
        body = null;
      }
    }
    if (body) {
      const ok = await copyText(body);
      showToast(ok ? "Prompt copied to your clipboard." : "Copying is blocked in this browser.", ok ? "success" : "error");
      return;
    }
    const ok = await copyText(item.idea ?? item.title);
    showToast(ok ? "Copied the prompt idea — open it in Studio for the full prompt." : "Copying is blocked in this browser.", ok ? "success" : "error");
  }

  // Phase 3Q: Open carries IDENTITY, not content. Studio fetches the prompt itself, so
  // reopening a saved prompt attaches to it (versions, evaluations, restore) instead of
  // seeding a blank draft that the next Save would have created as a duplicate prompt.
  function openInStudio(item: ApiPrompt) {
    router.push(`/studio?prompt=${encodeURIComponent(item.id)}`);
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-[1400px] px-6 pt-8 pb-16 sm:px-8">
        <PageHeader
          eyebrow="Library"
          title={
            <>
              Built for <em className="text-purple">whatever you’re building.</em>
            </>
          }
          description="Start from a proven recipe or pick up a prompt you saved from Studio."
          actions={
            <Button variant="primary" icon="plus" onClick={() => (window.location.href = "/studio")}>
              Create prompt
            </Button>
          }
        />

        {/* Recipes */}
        <section className="mt-8" aria-labelledby="recipes-heading">
          <div id="recipes-heading">
            <SectionHeader title="Starting recipes" hint="One click loads a starter idea into Studio" />
          </div>
          <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
            {recipes.map((recipe) => (
              <button
                key={recipe.title}
                type="button"
                onClick={() => {
                  sessionStorage.setItem(
                    "sparkprompt-draft",
                    JSON.stringify({ idea: recipe.idea, audience: recipe.audience, output: recipe.output }),
                  );
                  router.push("/studio");
                }}
                className={`group relative rounded-xl p-4 text-left text-[#453d57] transition hover:shadow-[0_8px_22px_rgba(40,30,70,.12)] ${recipe.className}`}
              >
                <span className="grid h-8 w-8 place-items-center rounded-lg bg-white/70 text-purple">
                  <Icon name="spark" size={16} />
                </span>
                <span className="absolute top-4 right-4 text-faint opacity-70 transition group-hover:translate-x-0.5 group-hover:opacity-100">
                  <Icon name="chevron-right" size={15} />
                </span>
                <strong className="mt-8 mb-1 block text-[13px] font-extrabold">{recipe.title}</strong>
                <small className="block text-[11px] text-[#6f6680]">{recipe.body}</small>
              </button>
            ))}
          </div>
        </section>

        {/* Session sparks */}
        {sparks.length ? (
          <section className="mt-8" aria-labelledby="recent-sparks">
            <div id="recent-sparks">
              <SectionHeader
                title="From this session"
                action={
                  <button
                    type="button"
                    onClick={() => {
                      clearSparks();
                      showToast("Session sparks cleared.");
                    }}
                    className="inline-flex items-center gap-1 text-xs font-semibold text-muted hover:text-danger"
                  >
                    <Icon name="trash" size={13} /> Clear all
                  </button>
                }
              />
            </div>
            <div className="flex flex-wrap gap-2">
              {sparks.map((spark) => (
                <button
                  key={spark.id}
                  type="button"
                  onClick={() => {
                    sessionStorage.setItem("sparkprompt-draft", JSON.stringify({ idea: spark.idea, audience: spark.role, output: "best" }));
                    router.push("/studio");
                  }}
                  className="flex max-w-full items-center gap-2 rounded-full border border-border bg-card py-1.5 pr-3.5 pl-2 text-left transition hover:border-[#c9c0e6]"
                >
                  <Icon name="spark" size={13} className="shrink-0 text-purple" />
                  <span className="truncate text-xs font-semibold text-ink-soft">{spark.idea}</span>
                  <span className="ml-1 shrink-0 text-[10px] text-faint">{roleNames[spark.role]}</span>
                </button>
              ))}
            </div>
          </section>
        ) : null}

        {/* Saved prompts */}
        <section className="mt-8" aria-labelledby="saved-heading">
          <div id="saved-heading">
            <SectionHeader title="Saved prompts" hint={savedPrompts === null ? "Loading…" : `${savedPrompts.length} in your workspace database`} />
          </div>

          {savedError ? (
            <ErrorState title="Could not load saved prompts" message={savedError} retry={() => void loadSaved()} />
          ) : savedPrompts === null ? (
            <ListSkeleton rows={4} />
          ) : (
            <>
              <div className="mb-4 max-w-md">
                <div className="relative">
                  <span className="pointer-events-none absolute inset-y-0 left-3 flex items-center text-faint">
                    <Icon name="search" size={16} />
                  </span>
                  <Input
                    aria-label="Search saved prompts"
                    placeholder="Search your prompts…"
                    value={query}
                    onChange={(event) => setQuery(event.target.value)}
                    className="pl-9"
                  />
                </div>
              </div>

              {savedPrompts.length === 0 ? (
                <Card className="p-0">
                  <EmptyState
                    icon="library"
                    title="No saved prompts yet"
                    body="Prompts you save from Studio land here, ready to copy or re-run. Create your first one."
                    action={
                      <Button variant="primary" icon="plus" onClick={() => (window.location.href = "/studio")}>
                        Create prompt
                      </Button>
                    }
                  />
                </Card>
              ) : filtered.length === 0 ? (
                <Card className="p-6 text-center text-sm text-muted">
                  No prompts match “{query}”. Try a different search.
                </Card>
              ) : (
                <div className="grid gap-2">
                  {filtered.map((item) => (
                    <article key={item.id} className="group flex flex-col gap-3 rounded-xl border border-border bg-card p-4 transition hover:border-[#c9c0e6] sm:flex-row sm:items-center">
                      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-lg bg-purple-soft text-purple">
                        <Icon name="spark" size={17} />
                      </span>
                      <div className="min-w-0 flex-1">
                        <div className="flex flex-wrap items-center gap-2">
                          <strong className="truncate text-[13px] font-bold text-ink">{item.title}</strong>
                          <Badge tone="purple">{AUDIENCE_SHORT[item.audience] ?? item.audience}</Badge>
                        </div>
                        {item.idea && item.idea !== item.title ? (
                          <p className="mt-1 line-clamp-2 text-xs leading-5 text-muted">{item.idea}</p>
                        ) : null}
                        {item.body ? <p className="mt-1 line-clamp-2 font-mono text-[11px] leading-5 text-faint">{item.body}</p> : null}
                        <p className="mt-1.5 flex items-center gap-1.5 text-[11px] text-faint">
                          <Icon name="clock" size={12} />
                          Updated {new Date(item.updated_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
                        </p>
                      </div>
                      <div className="flex shrink-0 items-center gap-1.5">
                        <Button variant="secondary" size="sm" icon="open" onClick={() => openInStudio(item)}>
                          Open
                        </Button>
                        <Button variant="secondary" size="sm" icon="copy" onClick={() => void copyPrompt(item)}>
                          Copy
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          icon="trash"
                          aria-label={`Delete ${item.title}`}
                          onClick={() => setDeleting(item)}
                          className="text-faint hover:text-danger"
                        />
                      </div>
                    </article>
                  ))}
                </div>
              )}
            </>
          )}
        </section>

        <Link href="/dashboard" className="mt-10 inline-flex items-center gap-1 text-xs font-semibold text-muted hover:text-purple">
          <Icon name="chevron-left" size={14} /> Back to dashboard
        </Link>
      </div>

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => (deletingBusy ? undefined : setDeleting(null))}
        onConfirm={() => void confirmDelete()}
        busy={deletingBusy}
        title="Delete prompt?"
        body={`“${deleting?.title ?? ""}” and its versions will be permanently removed from your workspace.`}
      />
    </AppShell>
  );
}