"use client";

import { useEffect, useMemo, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Icon } from "@/components/icons";
import { CardSkeleton, EmptyState, ErrorState } from "@/components/states";
import { Button, Card, ConfirmDialog, Input, Modal, PageHeader, SectionHeader, Textarea } from "@/components/ui";
import { api, ApiError, type ApiProject, type ApiPrompt } from "@/lib/api";
import { friendlyApiError } from "@/lib/errors";
import { useToast } from "@/lib/toast";

// Phase 3R (D4): one readable sentence per failure class — a project deleted elsewhere
// (404), a payload the schema rejected (422), or a server fault (5xx). Anything else
// falls back to the shared friendly phrasing; raw server payloads are never shown.
function projectErrorMessage(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.status === 404)
      return "This project no longer exists. Refresh to see the current list.";
    if (err.status === 422)
      return "That project could not be saved — check the name and description.";
    if (err.status >= 500)
      return "Something went wrong on the server. Please try again.";
  }
  return friendlyApiError(err);
}

export default function ProjectsPage() {
  const { showToast } = useToast();
  const [projects, setProjects] = useState<ApiProject[] | null>(null);
  const [prompts, setPrompts] = useState<ApiPrompt[]>([]);
  const [error, setError] = useState("");
  const [createOpen, setCreateOpen] = useState(false);
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [creating, setCreating] = useState(false);
  const [deleting, setDeleting] = useState<ApiProject | null>(null);
  const [deletingBusy, setDeletingBusy] = useState(false);
  // Phase 3R (D4): edit flow — the card's values seed the modal, and a successful save
  // closes it and re-lists from the server so the card always shows server truth.
  const [editing, setEditing] = useState<ApiProject | null>(null);
  const [editName, setEditName] = useState("");
  const [editDescription, setEditDescription] = useState("");
  const [updating, setUpdating] = useState(false);

  async function load() {
    setError("");
    setProjects(null);
    try {
      const [projectList, promptList] = await Promise.all([api.listProjects(), api.listPrompts()]);
      setProjects(projectList);
      setPrompts(promptList);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Could not load projects.");
      setProjects([]);
    }
  }

  useEffect(() => {
    void load();
  }, []);

  const counts = useMemo(() => {
    const map: Record<string, number> = {};
    for (const prompt of prompts) {
      map[prompt.project_id] = (map[prompt.project_id] ?? 0) + 1;
    }
    return map;
  }, [prompts]);

  async function createProject() {
    const trimmed = name.trim();
    if (!trimmed || creating) return;
    setCreating(true);
    try {
      await api.createProject({ name: trimmed, description: description.trim() || undefined });
      setName("");
      setDescription("");
      setCreateOpen(false);
      showToast("Project created.");
      void load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Could not create the project.", "error");
    } finally {
      setCreating(false);
    }
  }

  async function confirmDelete() {
    if (!deleting) return;
    setDeletingBusy(true);
    try {
      await api.deleteProject(deleting.id);
      showToast("Project deleted.");
      setDeleting(null);
      void load();
    } catch (err) {
      showToast(err instanceof Error ? err.message : "Could not delete the project.", "error");
    } finally {
      setDeletingBusy(false);
    }
  }

  // Phase 3R (D4): seed the edit modal from the card as stored.
  function startEdit(project: ApiProject) {
    setEditing(project);
    setEditName(project.name);
    setEditDescription(project.description ?? "");
  }

  async function saveProject() {
    const trimmed = editName.trim();
    if (!editing || !trimmed || updating) return;
    setUpdating(true);
    try {
      // The description key is always sent (null clears it): the server only applies
      // keys that are present, so an omitted key would silently keep the old value.
      await api.updateProject(editing.id, {
        name: trimmed,
        description: editDescription.trim() || null,
      });
      setEditing(null);
      showToast("Project updated.");
      void load();
    } catch (err) {
      showToast(projectErrorMessage(err), "error");
    } finally {
      setUpdating(false);
    }
  }

  return (
    <AppShell>
      <div className="mx-auto max-w-[1400px] px-6 pt-8 pb-16 sm:px-8">
        <PageHeader
          eyebrow="Projects"
          title={
            <>
              Projects, <em className="text-purple">without the clutter.</em>
            </>
          }
          description="Projects group saved prompts by body of work. Everything is stored in your local workspace database."
          actions={
            <Button variant="primary" icon="plus" onClick={() => setCreateOpen(true)}>
              New project
            </Button>
          }
        />

        {error ? (
          <div className="mt-6">
            <ErrorState title="Could not load projects" message={error} retry={() => void load()} />
          </div>
        ) : null}

        <section className="mt-8" aria-labelledby="projects-heading">
          <div id="projects-heading">
            <SectionHeader title="All projects" hint={projects === null ? "Loading…" : `${projects.length} total`} />
          </div>

          {projects === null ? (
            <CardSkeleton count={4} />
          ) : projects.length === 0 ? (
            <Card className="p-0">
              <EmptyState
                icon="folder"
                title="No projects yet"
                body="Projects give your prompts a home. Create one to start organizing work."
                action={
                  <Button variant="primary" icon="plus" onClick={() => setCreateOpen(true)}>
                    New project
                  </Button>
                }
              />
            </Card>
          ) : (
            <div className="grid gap-4 md:grid-cols-2 xl:grid-cols-3">
              {projects.map((project) => {
                const count = counts[project.id] ?? 0;
                return (
                  <Card key={project.id} className="flex flex-col p-5">
                    <div className="flex items-start justify-between gap-3">
                      <span className="grid h-10 w-10 shrink-0 place-items-center rounded-xl bg-purple-soft text-purple">
                        <Icon name="folder" size={19} />
                      </span>
                      <div className="flex shrink-0 items-center gap-1">
                        <Button
                          variant="ghost"
                          size="sm"
                          onClick={() => startEdit(project)}
                        >
                          Edit
                        </Button>
                        <Button
                          variant="ghost"
                          size="sm"
                          icon="trash"
                          aria-label={`Delete ${project.name}`}
                          onClick={() => setDeleting(project)}
                          className="text-faint hover:text-danger"
                        />
                      </div>
                    </div>
                    <h3 className="mt-3 truncate text-[15px] font-extrabold text-ink">{project.name}</h3>
                    <p className="mt-1.5 flex-1 text-[13px] leading-5 text-muted">{project.description || "No description."}</p>
                    <div className="mt-4 flex items-center justify-between border-t border-border pt-3">
                      <span className="inline-flex items-center gap-1.5 text-xs font-semibold text-muted">
                        <Icon name="spark" size={14} className="text-purple" />
                        {count} {count === 1 ? "prompt" : "prompts"}
                      </span>
                      <span className="text-[11px] text-faint">
                        Created {new Date(project.created_at).toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })}
                      </span>
                    </div>
                  </Card>
                );
              })}
            </div>
          )}
        </section>
      </div>

      <Modal open={createOpen} onClose={() => setCreateOpen(false)} title="New project">
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void createProject();
          }}
        >
          <label className="block text-xs font-bold text-ink-soft">
            Name
            <Input
              autoFocus
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={160}
              placeholder="e.g. Marketing site refresh"
              className="mt-1.5"
            />
          </label>
          <label className="mt-4 block text-xs font-bold text-ink-soft">
            Description <span className="font-normal text-faint">(optional)</span>
            <Textarea
              value={description}
              onChange={(event) => setDescription(event.target.value)}
              maxLength={2000}
              rows={3}
              placeholder="What is this body of work about?"
              className="mt-1.5 resize-none"
            />
          </label>
          <div className="mt-6 flex justify-end gap-2">
            <Button variant="secondary" size="sm" onClick={() => setCreateOpen(false)} disabled={creating}>
              Cancel
            </Button>
            <Button variant="primary" size="sm" icon="plus" type="submit" disabled={creating || !name.trim()}>
              {creating ? "Creating…" : "Create project"}
            </Button>
          </div>
        </form>
      </Modal>

      <Modal
        open={editing !== null}
        onClose={() => (updating ? undefined : setEditing(null))}
        title="Edit project"
      >
        <form
          onSubmit={(event) => {
            event.preventDefault();
            void saveProject();
          }}
        >
          <label className="block text-xs font-bold text-ink-soft">
            Name
            <Input
              autoFocus
              value={editName}
              onChange={(event) => setEditName(event.target.value)}
              maxLength={160}
              className="mt-1.5"
            />
          </label>
          <label className="mt-4 block text-xs font-bold text-ink-soft">
            Description <span className="font-normal text-faint">(optional)</span>
            <Textarea
              value={editDescription}
              onChange={(event) => setEditDescription(event.target.value)}
              maxLength={2000}
              rows={3}
              placeholder="What is this body of work about?"
              className="mt-1.5 resize-none"
            />
          </label>
          <div className="mt-6 flex justify-end gap-2">
            <Button
              variant="secondary"
              size="sm"
              onClick={() => setEditing(null)}
              disabled={updating}
            >
              Cancel
            </Button>
            <Button
              variant="primary"
              size="sm"
              icon="save"
              type="submit"
              disabled={updating || !editName.trim()}
            >
              {updating ? "Saving…" : "Save changes"}
            </Button>
          </div>
        </form>
      </Modal>

      <ConfirmDialog
        open={deleting !== null}
        onClose={() => (deletingBusy ? undefined : setDeleting(null))}
        onConfirm={() => void confirmDelete()}
        busy={deletingBusy}
        title="Delete project?"
        body={`“${deleting?.name ?? ""}” and the prompts inside it will be permanently removed.`}
      />
    </AppShell>
  );
}