"use client";

import { useState } from "react";

import { makeCodeExplanation, makeEmail, makeIdeas, makeSummary, tools } from "@/lib/tools";
import { Badge, Button, Card, Input, Select, Textarea } from "@/components/ui";
import { Icon } from "@/components/icons";
import { useToast } from "@/lib/toast";
import type { ToolId } from "@/lib/types";
import { copyText } from "@/lib/utils";

export function QuickTools() {
  const [active, setActive] = useState<ToolId>("summarize");
  const [result, setResult] = useState("");
  const { showToast } = useToast();
  const tool = tools.find((item) => item.id === active) ?? tools[0];

  function run() {
    const form = document.getElementById("tool-form") as HTMLFormElement | null;
    const data = new FormData(form ?? undefined);
    let next = "";
    if (active === "summarize") {
      next = makeSummary(String(data.get("summaryInput") ?? ""), (data.get("summaryLength") as "short" | "medium" | "long") ?? "medium", String(data.get("summaryFocus") ?? ""));
    } else if (active === "email") {
      next = makeEmail(String(data.get("emailTo") ?? ""), String(data.get("emailNotes") ?? ""), (data.get("emailTone") as "friendly" | "professional" | "direct") ?? "professional");
    } else if (active === "code") {
      next = makeCodeExplanation(String(data.get("codeInput") ?? ""), (data.get("codeLevel") as "beginner" | "technical") ?? "beginner", String(data.get("codeGoal") ?? ""));
    } else {
      next = makeIdeas(String(data.get("ideaTopic") ?? ""), String(data.get("ideaAudience") ?? ""), (data.get("ideaStyle") as "practical" | "content" | "campaign") ?? "practical");
    }
    setResult(next);
  }

  const fieldClass = "text-[12px] font-semibold text-ink-soft";

  return (
    <section className="mt-8">
      <div className="grid items-start gap-4 lg:grid-cols-[.72fr_1.28fr]">
        <div className="grid gap-2 sm:grid-cols-2 lg:grid-cols-1">
          {tools.map((item) => (
            <button
              key={item.id}
              type="button"
              onClick={() => {
                setActive(item.id);
                setResult("");
              }}
              aria-pressed={active === item.id}
              className={`flex items-center gap-3 rounded-xl border px-3.5 py-3 text-left transition ${
                active === item.id
                  ? "border-purple bg-purple-soft"
                  : "border-border bg-card hover:border-[#c7bfd9] hover:bg-paper dark:hover:bg-raised"
              }`}
            >
              <span
                className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${
                  active === item.id ? "bg-purple text-white" : "bg-purple-soft text-purple"
                }`}
              >
                <Icon name={item.id === "summarize" ? "layers" : item.id === "email" ? "open" : item.id === "code" ? "library" : "spark"} size={17} />
              </span>
              <span className="min-w-0">
                <strong className="block truncate text-[13px] font-bold text-ink">{item.title}</strong>
                <small className="block truncate text-[11px] text-muted">{item.description}</small>
              </span>
            </button>
          ))}
        </div>

        <Card className="p-6">
          <div className="mb-4">
            <div className="flex items-center gap-2">
              <span className="font-mono text-[10px] font-bold tracking-widest text-purple">{tool.badge}</span>
              <span className="text-faint">·</span>
              <Badge tone="neutral">Local helper</Badge>
            </div>
            <h3 className="mt-2 text-base font-extrabold text-ink">{tool.title}</h3>
            <p className="mt-0.5 text-[12px] text-muted">{tool.description}</p>
          </div>

          <form id="tool-form" className="grid grid-cols-2 gap-3 max-[560px]:grid-cols-1" onSubmit={(event) => event.preventDefault()}>
            {active === "summarize" ? (
              <>
                <label className={`col-span-2 grid gap-1.5 max-[560px]:col-span-1 ${fieldClass}`}>
                  Paste text to summarize
                  <Textarea name="summaryInput" rows={4} className="resize-y" />
                </label>
                <label className={`grid gap-1.5 ${fieldClass}`}>
                  Summary length
                  <Select name="summaryLength" defaultValue="medium">
                    <option value="short">Short — 2 key points</option>
                    <option value="medium">Balanced — 3 key points</option>
                    <option value="long">Detailed — 5 key points</option>
                  </Select>
                </label>
                <label className={`grid gap-1.5 ${fieldClass}`}>
                  Focus on
                  <Input name="summaryFocus" placeholder="Optional: decisions, actions, risks…" />
                </label>
              </>
            ) : null}
            {active === "email" ? (
              <>
                <label className={`grid gap-1.5 ${fieldClass}`}>
                  Recipient
                  <Input name="emailTo" />
                </label>
                <label className={`grid gap-1.5 ${fieldClass}`}>
                  Tone
                  <Select name="emailTone" defaultValue="professional">
                    <option value="friendly">Friendly and warm</option>
                    <option value="professional">Professional and clear</option>
                    <option value="direct">Direct and concise</option>
                  </Select>
                </label>
                <label className={`col-span-2 grid gap-1.5 max-[560px]:col-span-1 ${fieldClass}`}>
                  What should the email say?
                  <Textarea name="emailNotes" rows={4} className="resize-y" />
                </label>
              </>
            ) : null}
            {active === "code" ? (
              <>
                <label className={`col-span-2 grid gap-1.5 max-[560px]:col-span-1 ${fieldClass}`}>
                  Paste a code snippet
                  <Textarea name="codeInput" rows={4} className="resize-y font-mono text-xs" />
                </label>
                <label className={`grid gap-1.5 ${fieldClass}`}>
                  Explanation level
                  <Select name="codeLevel">
                    <option value="beginner">Beginner-friendly</option>
                    <option value="technical">Technical overview</option>
                  </Select>
                </label>
                <label className={`grid gap-1.5 ${fieldClass}`}>
                  What are you checking?
                  <Input name="codeGoal" placeholder="Optional" />
                </label>
              </>
            ) : null}
            {active === "ideas" ? (
              <>
                <label className={`col-span-2 grid gap-1.5 max-[560px]:col-span-1 ${fieldClass}`}>
                  Topic or challenge
                  <Textarea name="ideaTopic" rows={4} className="resize-y" />
                </label>
                <label className={`grid gap-1.5 ${fieldClass}`}>
                  Audience
                  <Input name="ideaAudience" placeholder="Optional" />
                </label>
                <label className={`grid gap-1.5 ${fieldClass}`}>
                  Idea type
                  <Select name="ideaStyle">
                    <option value="practical">Practical actions</option>
                    <option value="content">Content ideas</option>
                    <option value="campaign">Campaign angles</option>
                  </Select>
                </label>
              </>
            ) : null}
          </form>

          <div className="mt-4 flex gap-2">
            <Button variant="primary" icon="spark" onClick={run}>
              {tool.action}
            </Button>
            <Button
              variant="secondary"
              icon="copy"
              disabled={!result}
              onClick={async () => {
                const ok = await copyText(result);
                showToast(ok ? "Tool result copied to your clipboard." : "Copying is blocked in this browser.", ok ? "success" : "error");
              }}
            >
              Copy result
            </Button>
          </div>
          <pre
            className={`mt-4 min-h-[84px] overflow-auto rounded-xl border p-4 font-mono text-xs leading-relaxed whitespace-pre-wrap ${
              result
                ? "border-[#cdc2ee] bg-purple-faint text-ink-soft dark:border-[#43346b] dark:bg-[#241d33] dark:text-[#ded8ea]"
                : "border-dashed border-border bg-paper text-faint dark:bg-raised"
            }`}
          >
            {result || tool.empty}
          </pre>
        </Card>
      </div>
    </section>
  );
}