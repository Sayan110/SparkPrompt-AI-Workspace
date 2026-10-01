"use client";

import Link from "next/link";

import { AppShell } from "@/components/app-shell";
import { Icon, type IconName } from "@/components/icons";
import { QuickTools } from "@/components/quick-tools";
import { Badge, Card, PageHeader } from "@/components/ui";

// Phase 3R (D1): these capabilities SHIP today — they live in Magic Studio, not in the
// Lab. The copy says what each one actually does and where to find it; nothing here
// claims a capability the Lab itself does not expose.
const experiments: { icon: IconName; title: string; body: string }[] = [
  { icon: "layers", title: "Prompt testing", body: "Run a prompt from Magic Studio against a configured model and keep the output for review." },
  { icon: "server", title: "Model comparison", body: "Compare two evaluated results side by side — criteria, output, prompt, and metadata." },
  { icon: "check", title: "Evaluation", body: "Score a run with structured rules instead of gut feel, and keep every result in history." },
  { icon: "lab", title: "Experiments", body: "Run every saved version of one prompt through one evaluator and see what changed." },
];

export default function LabPage() {
  return (
    <AppShell>
      <div className="mx-auto max-w-[1400px] px-6 pt-8 pb-16 sm:px-8">
        <PageHeader
          eyebrow="Lab"
          title={
            <>
              Everyday tools today, <em className="text-purple">real AI experiments in Studio.</em>
            </>
          }
          description="The Quick Tools below are local helpers — no AI provider required. Prompt testing, evaluation, comparison, and experiments all ship today in Magic Studio."
        />

        <QuickTools />

        <section className="mt-12" aria-labelledby="experiments-heading">
          <div id="experiments-heading">
            <div className="mb-3 flex items-center justify-between">
              <h2 className="flex items-center gap-2 text-[13px] font-bold text-ink-soft">
                <span className="h-4 w-1 rounded-full bg-purple" aria-hidden /> Available in Magic Studio
              </h2>
              <Badge tone="neutral">Shipped</Badge>
            </div>
          </div>
          <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
            {experiments.map((item) => (
              <Card key={item.title} className="flex flex-col p-5">
                <span className="grid h-10 w-10 place-items-center rounded-xl bg-purple-soft text-purple">
                  <Icon name={item.icon} size={19} />
                </span>
                <h3 className="mt-3 text-sm font-extrabold text-ink">{item.title}</h3>
                <p className="mt-1.5 flex-1 text-[13px] leading-5 text-muted">{item.body}</p>
              </Card>
            ))}
          </div>
          <p className="mt-4 text-[12px] text-faint">
            These run in{" "}
            <Link href="/studio" className="font-semibold text-purple hover:underline">
              Magic Studio
            </Link>{" "}
            — open a saved prompt to test, evaluate, compare, or run an experiment on it. Which AI
            providers are available depends on your environment (see Settings); the Quick Tools above
            stay fully local either way.
          </p>
        </section>

        <Link href="/dashboard" className="mt-10 inline-flex items-center gap-1 text-xs font-semibold text-muted hover:text-purple">
          <Icon name="chevron-left" size={14} /> Back to dashboard
        </Link>
      </div>
    </AppShell>
  );
}
