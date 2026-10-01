"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { AppShell } from "@/components/app-shell";
import { MagicStudio } from "@/components/magic-studio";
import { Button, Modal, PageHeader } from "@/components/ui";
import {
  clearStoredPromptId,
  readPromptIdFromUrl,
  readStoredPromptId,
} from "@/lib/studio-attachment";
import type { Audience, OutputFormat } from "@/lib/types";

type Draft = { idea: string; audience: Audience; output: OutputFormat };

export default function StudioPage() {
  const router = useRouter();
  const [helpOpen, setHelpOpen] = useState(false);
  const [resetKey, setResetKey] = useState(0);
  const [draft, setDraft] = useState<Draft | null>(null);
  // Phase 3Q: the saved prompt Studio must attach to, resolved once on mount.
  // Priority is URL → sessionStorage → unattached: an explicit ?prompt= always wins
  // over stale session state, and a session id resumes after reload/navigation.
  const [attachId, setAttachId] = useState<string | null>(null);
  const [ready, setReady] = useState(false);

  useEffect(() => {
    const target = readPromptIdFromUrl() ?? readStoredPromptId();
    try {
      if (target) {
        // Attaching wins: drop any one-shot new-prompt draft seed so it can never be
        // mixed into (or saved over) the prompt being opened. The server response
        // hydrates the editor instead.
        sessionStorage.removeItem("sparkprompt-draft");
      } else {
        const raw = sessionStorage.getItem("sparkprompt-draft");
        if (raw) {
          setDraft(JSON.parse(raw) as Draft);
          sessionStorage.removeItem("sparkprompt-draft");
        }
      }
    } catch {
      setDraft(null);
    }
    setAttachId(target);
    setReady(true);
  }, []);

  return (
    <AppShell>
      <div className="mx-auto max-w-[1400px] px-6 pt-8 sm:px-8">
        <PageHeader
          eyebrow="Magic Studio"
          title={
            <>
              Turn a rough thought into <em className="text-purple">a remarkable prompt.</em>
            </>
          }
          description="Describe what you need in everyday language. SparkPrompt adds the context, structure, and detail that great AI responses need."
          actions={
            <>
              <Button
                variant="secondary"
                icon="plus"
                onClick={() => {
                  // Phase 3Q: starting a new prompt is the one intentional way to
                  // detach. Both identity sources are dropped so neither can resume
                  // the old prompt on the next mount.
                  clearStoredPromptId();
                  setAttachId(null);
                  setDraft(null);
                  setResetKey((value) => value + 1);
                  if (readPromptIdFromUrl()) router.replace("/studio");
                }}
              >
                New prompt
              </Button>
              <Button variant="ghost" icon="info" onClick={() => setHelpOpen(true)}>
                How it works
              </Button>
            </>
          }
        />
      </div>
      {ready ? (
        <div className="mt-6">
          <MagicStudio
            // Phase 3Q: the attachment target is part of the key, so a different target
            // always starts a fresh Studio rather than inheriting another prompt's state.
            key={`${resetKey}-${attachId ?? "new"}-${draft?.idea ?? "blank"}`}
            initialIdea={draft?.idea ?? ""}
            initialAudience={draft?.audience ?? "everyone"}
            initialOutput={draft?.output ?? "best"}
            initialPromptId={attachId}
          />
        </div>
      ) : null}

      <Modal open={helpOpen} onClose={() => setHelpOpen(false)} title="How SparkPrompt works">
        <ol className="list-decimal space-y-2 pl-5 text-sm leading-6 text-muted">
          <li>We identify the goal, audience, and hidden decisions from your rough idea.</li>
          <li>We add useful constraints and a clear output structure.</li>
          <li>You get one polished prompt, ready to paste into any AI tool.</li>
        </ol>
        <p className="mt-4 rounded-xl bg-purple-faint p-4 text-sm leading-6 text-ink-soft">
          <strong className="text-ink">Start messy.</strong> Even a few words work — you can tune the role, format, and depth afterwards.
        </p>
        <div className="mt-6 flex justify-end">
          <Button variant="primary" size="sm" onClick={() => setHelpOpen(false)}>
            Got it
          </Button>
        </div>
      </Modal>
    </AppShell>
  );
}