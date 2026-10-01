"use client";

import { useEffect, useState } from "react";

import { AppShell } from "@/components/app-shell";
import { Icon, type IconName } from "@/components/icons";
import { ErrorState } from "@/components/states";
import { Badge, Card, PageHeader, SectionHeader } from "@/components/ui";
import { api, type ApiAiProvider } from "@/lib/api";
import { friendlyApiError } from "@/lib/errors";
import { useTheme } from "@/lib/theme";
import { useWorkspace } from "@/lib/workspace";

type Status = "checking" | "online" | "offline";

function StatusRow({ icon, label, status, detail }: { icon: IconName; label: string; status: Status; detail: string }) {
  const tone =
    status === "online"
      ? "bg-success-soft text-success"
      : status === "offline"
        ? "bg-danger-soft text-danger"
        : "bg-paper text-faint dark:bg-raised";
  return (
    <div className="flex items-center gap-3 py-3">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-purple-soft text-purple">
        <Icon name={icon} size={17} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-bold text-ink">{label}</p>
        <p className="truncate font-mono text-[11px] text-faint">{detail}</p>
      </div>
      <span className={`inline-flex items-center gap-1.5 rounded-md px-2 py-1 text-[11px] font-bold ${tone}`}>
        <span className="h-1.5 w-1.5 rounded-full bg-current" aria-hidden />
        {status === "checking" ? "Checking" : status === "online" ? "Online" : "Offline"}
      </span>
    </div>
  );
}

// Phase 3R (D5): one factual row per registered provider, built ONLY from what
// GET /api/ai/providers reports. Nothing here is inferred, no provider is contacted to
// produce the status, and no credential is rendered — the endpoint never returns one.
function ProviderRow({ provider }: { provider: ApiAiProvider }) {
  const models = provider.models.slice(0, 3).join(", ");
  const extra = provider.models.length - 3;
  return (
    <li className="flex flex-wrap items-center gap-3 py-3">
      <span className="grid h-9 w-9 shrink-0 place-items-center rounded-lg bg-purple-soft text-purple">
        <Icon name="server" size={17} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-[13px] font-bold text-ink">{provider.name}</p>
        <p
          className="truncate font-mono text-[11px] text-faint"
          title={provider.models.join(", ") || "No models reported"}
        >
          {provider.models.length === 0
            ? "No models reported"
            : `${models}${extra > 0 ? ` +${extra} more` : ""}${
                provider.default_model ? ` · default ${provider.default_model}` : ""
              }`}
        </p>
      </div>
      <div className="flex shrink-0 flex-wrap items-center gap-1.5">
        <Badge tone={provider.configured ? "success" : "neutral"}>
          {provider.configured ? "Configured" : "Not configured"}
        </Badge>
        <Badge tone={provider.available ? "success" : "danger"}>
          {provider.available ? "Available" : "Unavailable"}
        </Badge>
        <Badge tone={provider.streaming ? "purple" : "neutral"}>
          {provider.streaming ? "Streaming" : "No streaming"}
        </Badge>
      </div>
    </li>
  );
}

export default function SettingsPage() {
  const { user } = useWorkspace();
  const { theme, system, toggleTheme } = useTheme();
  const [apiStatus, setApiStatus] = useState<Status>("checking");
  const [apiDetail, setApiDetail] = useState("Pinging /api/health…");
  const [dbStatus, setDbStatus] = useState<Status>("checking");
  const [dbDetail, setDbDetail] = useState("Checking the workspace database…");
  // Phase 3R (D5): provider registry status. One request covers everything the UI
  // shows — configured/available/streaming flags plus the model list — so there is no
  // second call to duplicate it, and nothing polls.
  const [providers, setProviders] = useState<ApiAiProvider[] | null>(null);
  const [providersLoading, setProvidersLoading] = useState(false);
  const [providersError, setProvidersError] = useState("");

  async function checkSystem() {
    setApiStatus("checking");
    setDbStatus("checking");
    setApiDetail("Pinging /api/health…");
    setDbDetail("Checking the workspace database…");

    try {
      const health = await api.health();
      setApiStatus("online");
      setApiDetail(`${health.service} · ${health.status}`);
    } catch (err) {
      setApiStatus("offline");
      setApiDetail(err instanceof Error ? err.message : "API unreachable on port 8000.");
    }

    try {
      const prompts = await api.listPrompts();
      setDbStatus("online");
      setDbDetail(`PostgreSQL reachable · ${prompts.length} saved prompts`);
    } catch (err) {
      setDbStatus("offline");
      setDbDetail(err instanceof Error ? err.message : "Database connection failed.");
    }
  }

  // Phase 3R (D5): a failed status load is contained to this section — the ErrorState
  // offers a retry and the rest of Settings keeps rendering normally.
  async function loadProviders() {
    setProvidersLoading(true);
    setProvidersError("");
    try {
      setProviders(await api.aiProviders());
    } catch (err) {
      setProviders(null);
      setProvidersError(friendlyApiError(err));
    } finally {
      setProvidersLoading(false);
    }
  }

  useEffect(() => {
    void checkSystem();
    void loadProviders();
  }, []);

  return (
    <AppShell>
      <div className="mx-auto max-w-[1000px] px-6 pt-8 pb-16 sm:px-8">
        <PageHeader
          eyebrow="Settings"
          title={
            <>
              Preferences and <em className="text-purple">system status.</em>
            </>
          }
          description="Appearance is stored locally. Everything else reflects the live workspace services."
        />

        <div className="mt-8 grid gap-8">
          <section aria-labelledby="appearance-heading">
            <div id="appearance-heading">
              <SectionHeader title="Appearance" />
            </div>
            <Card className="p-5">
              <div className="flex flex-wrap items-center justify-between gap-4">
                <div>
                  <p className="text-sm font-bold text-ink">Theme</p>
                  <p className="mt-1 text-[13px] text-muted">
                    Currently <strong className="text-ink">{theme === "dark" ? "Dark" : "Light"}</strong>
                    {system === theme ? " · matches your system preference" : ""}.
                  </p>
                </div>
                <div className="flex gap-2">
                  <button
                    type="button"
                    onClick={() => (theme !== "light" ? toggleTheme() : undefined)}
                    aria-pressed={theme === "light"}
                    className={`flex items-center gap-2 rounded-xl border px-4 py-2.5 text-[13px] font-bold transition ${
                      theme === "light" ? "border-purple bg-purple-soft text-purple" : "border-border bg-card text-muted hover:border-[#c7bfd9]"
                    }`}
                  >
                    <Icon name="sun" size={16} /> Light
                  </button>
                  <button
                    type="button"
                    onClick={() => (theme !== "dark" ? toggleTheme() : undefined)}
                    aria-pressed={theme === "dark"}
                    className={`flex items-center gap-2 rounded-xl border px-4 py-2.5 text-[13px] font-bold transition ${
                      theme === "dark" ? "border-purple bg-purple-soft text-purple" : "border-border bg-card text-muted hover:border-[#c7bfd9]"
                    }`}
                  >
                    <Icon name="moon" size={16} /> Dark
                  </button>
                </div>
              </div>
            </Card>
          </section>

          <section aria-labelledby="workspace-heading">
            <div id="workspace-heading">
              <SectionHeader title="Workspace" />
            </div>
            <Card className="p-5">
              <dl className="divide-y divide-border">
                <div className="flex items-center justify-between gap-4 py-3">
                  <dt className="text-[13px] font-semibold text-muted">Name</dt>
                  <dd className="text-[13px] font-bold text-ink">{user?.displayName ?? "Account"}</dd>
                </div>
                <div className="flex items-center justify-between gap-4 py-3">
                  <dt className="text-[13px] font-semibold text-muted">Email</dt>
                  <dd className="truncate text-[13px] font-bold text-ink">{user?.email ?? "Not provided"}</dd>
                </div>
                <div className="flex items-center justify-between gap-4 py-3">
                  <dt className="text-[13px] font-semibold text-muted">Session</dt>
                  <dd className="text-[13px] font-bold text-ink">Signed in</dd>
                </div>
              </dl>
              <p className="mt-3 rounded-xl bg-purple-faint p-4 text-[12px] leading-6 text-ink-soft">
                Your account lives in PostgreSQL behind a salted scrypt hash. The session is an HttpOnly cookie: signing out revokes it server-side.
              </p>
            </Card>
          </section>

          <section aria-labelledby="system-heading">
            <div id="system-heading">
              <SectionHeader
                title="System status"
                hint={
                  <>
                    <span className="inline-flex items-center gap-1">
                      <Icon name="refresh" size={12} />
                      <button type="button" className="font-semibold text-purple hover:underline" onClick={() => void checkSystem()} aria-label="Re-check system status">
                        Re-check
                      </button>
                    </span>
                  </>
                }
              />
            </div>
            <Card className="divide-y divide-border p-5 pt-1" role="group" aria-label="Service status">
              <StatusRow icon="dashboard" label="Frontend" status="online" detail="Next.js dev server · localhost:3000" />
              <StatusRow icon="server" label="API" status={apiStatus} detail={apiDetail} />
              <StatusRow icon="database" label="Database" status={dbStatus} detail={dbDetail} />
            </Card>
          </section>

          {/* Phase 3R (D5): factual provider status from the existing registry endpoint. */}
          <section aria-labelledby="providers-heading">
            <div id="providers-heading">
              <SectionHeader
                title="AI Providers"
                hint={
                  <span className="inline-flex items-center gap-1">
                    <Icon name="refresh" size={12} />
                    <button
                      type="button"
                      className="font-semibold text-purple hover:underline"
                      onClick={() => void loadProviders()}
                      aria-label="Refresh AI provider status"
                    >
                      Refresh
                    </button>
                  </span>
                }
              />
            </div>
            <Card className="p-5 pt-1" role="group" aria-label="AI provider status">
              {providersLoading && providers === null ? (
                <p className="py-3 text-[13px] text-muted">Loading provider status…</p>
              ) : providersError ? (
                <div className="py-3">
                  <ErrorState
                    title="Provider status unavailable"
                    message={providersError}
                    retry={() => void loadProviders()}
                  />
                </div>
              ) : !providers || providers.length === 0 ? (
                <p className="py-3 text-[13px] text-muted">
                  No providers are registered in this environment.
                </p>
              ) : (
                <ul className="divide-y divide-border">
                  {providers.map((provider) => (
                    <ProviderRow key={provider.id} provider={provider} />
                  ))}
                </ul>
              )}
              <p className="mt-3 border-t border-border pt-3 text-[11px] leading-5 text-faint">
                Provider support is built into the AI Gateway; whether a provider is
                configured depends on this environment. Status is read from{" "}
                <code className="font-mono">/api/ai/providers</code> only — no provider is
                called to produce it, and no API key is stored or displayed here.
              </p>
            </Card>
          </section>
        </div>
      </div>
    </AppShell>
  );
}