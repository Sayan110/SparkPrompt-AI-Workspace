"use client";

import { useEffect, useMemo, useState } from "react";

import { Icon } from "@/components/icons";
import { CardSkeleton, EmptyState } from "@/components/states";
import { Badge, Button, Card, ConfirmDialog, Input, Select, Textarea } from "@/components/ui";
import {
  api,
  ApiError,
  type ApiAiProvider,
  type ApiPrompt,
  type ApiPromptVersion,
  type ComparisonInput,
  type ComparisonResult,
  type CriterionDiff,
  type CriterionVerdict,
  type DiffLine,
  type EvaluationHistoryItem,
  type EvaluationInput,
  type EvaluationRecordDetail,
  type EvaluationResult,
  type EvaluationRuleInput,
  type EvaluationRuleType,
  type EvaluationSuiteInput,
  type EvaluationSuiteResult,
  type EvaluationSuiteTarget,
  type EvaluatorInput,
  type ExperimentResult,
  type FieldDiff,
  type PromptAnalysis,
  type PromptCreation,
  type PromptEnhancement,
  type PromptTestResult,
  type RuleWeightInput,
  type ScoringInput,
  type ScoringMode,
  type UsageFieldDiff,
} from "@/lib/api";
import { examples } from "@/lib/content";
import { friendlyApiError } from "@/lib/errors";
import {
  historySelectionChoice,
  historyToEvaluationResult,
  type CompareSource,
  type CompareSources,
} from "@/lib/evaluation-compare";
import {
  createEnhancedPrompt,
  depthGuidance,
  escapeHtml,
  roleNames,
  shortTitle,
} from "@/lib/prompt-engine";
import {
  clearStoredPromptId,
  storePromptId,
} from "@/lib/studio-attachment";
import { useToast } from "@/lib/toast";
import type { Audience, DepthLevel, OutputFormat } from "@/lib/types";
import { copyText } from "@/lib/utils";
import { useWorkspace } from "@/lib/workspace";

function renderPromptHtml(prompt: string): string {
  return escapeHtml(prompt)
    .replace(
      /^Act as (.+)$/m,
      '<span class="block mb-2 font-mono text-[10px] font-bold tracking-[1px] text-purple">GENERATED PROMPT</span>Act as <span class="font-bold">$1</span>',
    )
    .replace(
      /^## (.+)$/gm,
      '<span class="block mt-4 text-[11px] font-extrabold tracking-wide text-purple-dark dark:text-[#cbbcff]">$1</span>',
    );
}

/* ------------------------------------------------------------------ */
/* Phase 3E: evaluation rule model (mirrors backend app/evaluation)   */
/* ------------------------------------------------------------------ */

const RULE_TYPES: { value: EvaluationRuleType; label: string }[] = [
  { value: "contains", label: "Contains text" },
  { value: "not_contains", label: "Does not contain text" },
  { value: "min_length", label: "Minimum length" },
  { value: "max_length", label: "Maximum length" },
  { value: "exact_match", label: "Exactly matches expected" },
  { value: "normalized_match", label: "Matches expected (normalized)" },
  { value: "regex_match", label: "Matches regex pattern" },
];

const MATCH_RULE_TYPES = new Set<EvaluationRuleType>([
  "exact_match",
  "normalized_match",
]);
const TEXT_RULE_TYPES = new Set<EvaluationRuleType>([
  "contains",
  "not_contains",
]);
const LENGTH_RULE_TYPES = new Set<EvaluationRuleType>([
  "min_length",
  "max_length",
]);
const CASE_SENSITIVE_RULE_TYPES = new Set<EvaluationRuleType>([
  "contains",
  "not_contains",
  "regex_match",
]);

function newRuleId(): string {
  if (
    typeof crypto !== "undefined" &&
    typeof crypto.randomUUID === "function"
  ) {
    return crypto.randomUUID();
  }
  return `rule-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 10)}`;
}

function ruleLabel(type: EvaluationRuleType): string {
  return RULE_TYPES.find((option) => option.value === type)?.label ?? type;
}

function evidenceSummary(evidence: CriterionVerdict["evidence"]): string {
  const matched = evidence.matched;
  if (typeof matched === "boolean") {
    const text =
      typeof evidence.text === "string" ? ` — \"${evidence.text}\"` : "";
    return `${matched ? "Matched" : "No match"}${text}`;
  }
  if (
    typeof evidence.actual_length === "number" &&
    typeof evidence.minimum === "number"
  ) {
    return `Length ${evidence.actual_length} ≥ min ${evidence.minimum}`;
  }
  if (
    typeof evidence.actual_length === "number" &&
    typeof evidence.maximum === "number"
  ) {
    return `Length ${evidence.actual_length} ≤ max ${evidence.maximum}`;
  }
  if (
    typeof evidence.actual_length === "number" &&
    typeof evidence.expected_length === "number"
  ) {
    return `Expected ${evidence.expected_length} chars, got ${evidence.actual_length}`;
  }
  return "—";
}

/** A rule in the Studio editor always carries a client-side id (server echoes it back). */
type StudioRule = EvaluationRuleInput & { id: string };

function AnalysisList({ title, items }: { title: string; items: string[] }) {
  return (
    <div>
      <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
        {title}
      </p>
      {items.length === 0 ? (
        <p className="mt-0.5 text-[12px] text-muted">None identified.</p>
      ) : (
        <ul className="mt-1 list-disc space-y-1 pl-4 text-[12px] leading-relaxed text-muted">
          {items.map((item, index) => (
            <li key={index}>{item}</li>
          ))}
        </ul>
      )}
    </div>
  );
}

// Phase 3Q: prompt metadata is a free-form string on the server (max 40 chars) while
// the editor's state is a closed union. Hydration therefore normalises an unexpected
// stored value back to the default instead of casting it into a state it cannot be.
const AUDIENCE_VALUES: readonly string[] = [
  "everyone",
  "developer",
  "creator",
  "business",
  "student",
];
const OUTPUT_VALUES: readonly string[] = [
  "best",
  "steps",
  "detailed",
  "concise",
  "table",
  "code",
];

function isAudience(value: string): value is Audience {
  return AUDIENCE_VALUES.includes(value);
}

function isOutputFormat(value: string): value is OutputFormat {
  return OUTPUT_VALUES.includes(value);
}

export function MagicStudio({
  initialIdea = "",
  initialAudience = "everyone",
  initialOutput = "best",
  initialPromptId = null,
}: {
  initialIdea?: string;
  initialAudience?: Audience;
  initialOutput?: OutputFormat;
  // Phase 3Q: id of the saved prompt this Studio is attached to (null = new prompt).
  // The caller resolves it from the URL / session store; everything else about the
  // prompt is fetched from the server by the attach effect below.
  initialPromptId?: string | null;
}) {
  const { addSpark } = useWorkspace();
  const { showToast } = useToast();
  const [idea, setIdea] = useState(initialIdea);
  const [audience, setAudience] = useState<Audience>(initialAudience);
  const [output, setOutput] = useState<OutputFormat>(initialOutput);
  const [depth, setDepth] = useState<DepthLevel>(2);
  const [prompt, setPrompt] = useState("");
  const [saved, setSaved] = useState(false);
  const [saving, setSaving] = useState(false);
  // Phase 3I: version number of the body currently in the editor, as reported by the
  // server. Never counted locally — the prompt may have been saved in another session.
  const [savedVersion, setSavedVersion] = useState<number | null>(null);
  const [busy, setBusy] = useState(false);
  const [providers, setProviders] = useState<ApiAiProvider[]>([]);
  const [aiProvider, setAiProvider] = useState<string | null>(null);
  const [aiModel, setAiModel] = useState<string | null>(null);
  const [enhancing, setEnhancing] = useState(false);
  const [enhancement, setEnhancement] = useState<PromptEnhancement | null>(
    null,
  );
  const [analysis, setAnalysis] = useState<PromptAnalysis | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [createGoal, setCreateGoal] = useState("");
  const [createContext, setCreateContext] = useState("");
  const [creation, setCreation] = useState<PromptCreation | null>(null);
  const [creating, setCreating] = useState(false);
  const [testInput, setTestInput] = useState("");
  const [testResult, setTestResult] = useState<PromptTestResult | null>(null);
  const [testing, setTesting] = useState(false);
  // Phase 3E: deterministic evaluation state.
  const [evalExpanded, setEvalExpanded] = useState(false);
  const [evaluating, setEvaluating] = useState(false);
  const [evalResult, setEvalResult] = useState<EvaluationResult | null>(null);
  const [evalRules, setEvalRules] = useState<StudioRule[]>([
    { id: newRuleId(), type: "contains", text: "", case_sensitive: true },
  ]);
  const [evalExpected, setEvalExpected] = useState("");
  // Phase 3L: scoring profile for the next evaluation. Equal criteria is the
  // default (exact Phase 3K behavior); weighted criteria attaches one positive
  // weight per rule id. Raw strings keep the inputs controlled; the server
  // stays authoritative for validation and computes the score.
  const [scoringMode, setScoringMode] = useState<ScoringMode>("unweighted");
  const [ruleWeights, setRuleWeights] = useState<Record<string, string>>({});
  // Phase 3F: comparison state (left/right captures + the deterministic result).
  const [compareExpanded, setCompareExpanded] = useState(false);
  const [comparing, setComparing] = useState(false);
  const [compareLeft, setCompareLeft] = useState<EvaluationResult | null>(null);
  const [compareRight, setCompareRight] = useState<EvaluationResult | null>(
    null,
  );
  const [comparison, setComparison] = useState<ComparisonResult | null>(null);
  // Phase 3R: which mode each slot must use (see CompareSource). A slot filled from
  // Evaluation History is always compared as stored; a captured live result keeps
  // Phase 3F's original run-pair/supplied choice.
  const [compareSources, setCompareSources] = useState<CompareSources>({
    left: "capture",
    right: "capture",
  });
  // Phase 3R: the history row currently being fetched into a slot (shows "Loading…").
  const [compareHistoryId, setCompareHistoryId] = useState<string | null>(null);
  // Phase 3G: evaluation suite state (one evaluator, many targets, counts only).
  const [suiteExpanded, setSuiteExpanded] = useState(false);
  const [suiteRunning, setSuiteRunning] = useState(false);
  const [suiteTargets, setSuiteTargets] = useState<string[]>([""]);
  const [suiteResult, setSuiteResult] = useState<EvaluationSuiteResult | null>(
    null,
  );
  // Phase 3H: prompt experiment state (one evaluator, every saved version, counts only).
  const [experimentExpanded, setExperimentExpanded] = useState(false);
  const [experimentRunning, setExperimentRunning] = useState(false);
  const [experimentPrompts, setExperimentPrompts] = useState<ApiPrompt[]>([]);
  const [experimentPromptId, setExperimentPromptId] = useState("");
  const [experimentResult, setExperimentResult] = useState<ExperimentResult | null>(
    null,
  );
  // Id of the prompt this session is attached to, so the experiment lane can default to it.
  const [savedPromptId, setSavedPromptId] = useState<string | null>(null);
  // Phase 3Q: true from the moment a saved-prompt target is known until the prompt has
  // been fetched from the server (or the attempt failed). Initialised straight from the
  // prop, so the very first frame already shows "opening…" rather than a blank new
  // prompt that could be mistaken for — or saved over — the prompt being opened.
  const [attaching, setAttaching] = useState(Boolean(initialPromptId));
  // Phase 3J: version history of the saved prompt, exactly as the server reports it.
  // The list drives display only — every number shown comes from the response, and the
  // preview never touches the editor draft.
  const [historyVersions, setHistoryVersions] = useState<ApiPromptVersion[] | null>(null);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [previewVersionId, setPreviewVersionId] = useState<string | null>(null);
  const [restoreTarget, setRestoreTarget] = useState<ApiPromptVersion | null>(null);
  const [restoring, setRestoring] = useState(false);
  // Phase 3P: saved Evaluation History for the current prompt. Server truth only —
  // the list is a bounded locator (no verdict evidence) and the detail panel shows
  // the stored snapshots/verdicts exactly as they were recorded. Nothing here can
  // edit a stored evaluation: the API is append-only, so there is no edit/delete.
  const [evalHistory, setEvalHistory] = useState<EvaluationHistoryItem[] | null>(null);
  const [evalHistoryLoading, setEvalHistoryLoading] = useState(false);
  const [evalDetailId, setEvalDetailId] = useState<string | null>(null);
  const [evalDetail, setEvalDetail] = useState<EvaluationRecordDetail | null>(null);
  const [evalDetailLoading, setEvalDetailLoading] = useState(false);

  const ready = Boolean(prompt);
  const availableProviders = providers.filter((provider) => provider.available);
  const selectedModels =
    providers.find((provider) => provider.id === aiProvider)?.models ?? [];

  useEffect(() => {
    let cancelled = false;
    api
      .aiProviders()
      .then((list) => {
        if (cancelled) return;
        setProviders(list);
        const firstAvailable = list.find((provider) => provider.available);
        if (firstAvailable) {
          setAiProvider(firstAvailable.id);
          setAiModel(
            firstAvailable.default_model ?? firstAvailable.models[0] ?? null,
          );
        }
      })
      .catch(() => {
        if (!cancelled) setProviders([]);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Phase 3H: the experiment lane is collapsed by default, so the saved-prompt list is
  // fetched the first time it is opened rather than on every Studio render.
  useEffect(() => {
    if (!experimentExpanded) return;
    let cancelled = false;
    api
      .listPrompts()
      .then((list) => {
        if (cancelled) return;
        setExperimentPrompts(list);
        setExperimentPromptId((current) => current || savedPromptId || list[0]?.id || "");
      })
      .catch(() => {
        if (!cancelled) setExperimentPrompts([]);
      });
    return () => {
      cancelled = true;
    };
  }, [experimentExpanded, savedPromptId]);

  // Phase 3Q: attach to an existing saved prompt. The id arrives from Library via
  // `?prompt=` (or from this tab's session store after a reload/navigation); the body,
  // metadata, current version, version history, and evaluation history are all fetched
  // here. GETs only — attaching never creates a prompt, so reopening one cannot
  // duplicate it. Deps are the target id alone: every helper below is a stable
  // per-render closure, and re-running on those would refetch on every keystroke.
  useEffect(() => {
    if (!initialPromptId) return;
    let cancelled = false;
    setAttaching(true);
    void (async () => {
      try {
        const existing = await api.getPrompt(initialPromptId);
        if (cancelled) return;
        // Server response is the source of truth: it replaces whatever the editor held.
        setSavedPromptId(existing.id);
        setSavedVersion(existing.version_number ?? null);
        setPrompt(existing.body ?? "");
        setIdea(existing.idea);
        setAudience(isAudience(existing.audience) ? existing.audience : "everyone");
        setOutput(
          isOutputFormat(existing.output_format) ? existing.output_format : "best",
        );
        setDepth(
          existing.depth === 1 || existing.depth === 3 ? existing.depth : 2,
        );
        setSaved(Boolean(existing.body));
        setPreviewVersionId(null);
        setRestoreTarget(null);
        setEvalDetailId(null);
        setEvalDetail(null);
        setEvalHistory(null);
        // Identity-only session memory, so a reload resumes this same prompt.
        storePromptId(existing.id);
        // Both histories belong to this prompt and are available immediately.
        void loadHistory(existing.id);
        void loadEvaluationHistory(existing.id);
      } catch (error) {
        if (cancelled) return;
        detachAttachment(
          error instanceof ApiError && error.status === 404
            ? "That saved prompt is no longer available. Start a new one when you're ready."
            : friendlyApiError(error),
        );
      } finally {
        if (!cancelled) setAttaching(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [initialPromptId]);

  function selectProvider(providerId: string) {
    setAiProvider(providerId);
    const provider = providers.find((item) => item.id === providerId);
    setAiModel(provider?.default_model ?? provider?.models[0] ?? null);
  }

  function generate(nextDepth = depth) {
    const trimmed = idea.trim();
    if (!trimmed) {
      showToast("Start with your rough idea — even a few words work.", "error");
      return;
    }
    setBusy(true);
    const created = createEnhancedPrompt({
      idea: trimmed,
      audience,
      output,
      depth: nextDepth,
    });
    setPrompt(created);
    addSpark({ idea: trimmed, role: audience, prompt: created });
    setSaved(false);
    setSavedVersion(null);
    setPreviewVersionId(null);
    setRestoreTarget(null);
    setBusy(false);
  }

  function clearAll() {
    setIdea("");
    setPrompt("");
    setSaved(false);
    setSavedVersion(null);
    setPreviewVersionId(null);
    setRestoreTarget(null);
  }

  async function copyPrompt() {
    if (!prompt) return;
    const ok = await copyText(prompt);
    showToast(
      ok
        ? "Prompt copied to your clipboard."
        : "Copying is blocked in this browser. Select the text to copy it.",
      ok ? "success" : "error",
    );
  }

  // Phase 3Q: drop every trace of an attachment (editor-facing state plus the stored
  // identity) so Studio falls back to unattached "new prompt" mode. It never creates a
  // replacement prompt — that stays the user's explicit next action.
  function detachAttachment(message: string) {
    setSavedPromptId(null);
    setSavedVersion(null);
    setSaved(false);
    setHistoryVersions(null);
    setPreviewVersionId(null);
    setRestoreTarget(null);
    setEvalHistory(null);
    setEvalDetailId(null);
    setEvalDetail(null);
    clearStoredPromptId();
    showToast(message, "error");
  }

  async function saveToWorkspace() {
    // Phase 3Q: while an attach is in flight savedPromptId is still null, and a POST
    // here would duplicate the prompt being opened. Attach first, save after.
    if (!prompt || saving || attaching) return;
    const trimmed = idea.trim();
    if (!trimmed) return;
    setSaving(true);
    try {
      // First save creates the prompt. Every later save in this session sends the body to
      // the same prompt, which appends a new version server-side — so repeated saves
      // build a history instead of scattering duplicate prompts through the library.
      const existingId = savedPromptId;
      const savedPrompt = existingId
        ? await api.updatePrompt(existingId, { body: prompt })
        : await api.createPrompt({
            title: shortTitle(trimmed),
            idea: trimmed,
            audience,
            output_format: output,
            depth,
            body: prompt,
          });
      setSaved(true);
      setSavedVersion(savedPrompt.version_number ?? null);
      // Remember which saved prompt this is so the experiment lane can default to it.
      setSavedPromptId(savedPrompt.id);
      // Phase 3Q: a save makes this a saved prompt for this tab too, so a reload
      // resumes it (and its history) instead of falling back to an unattached editor.
      storePromptId(savedPrompt.id);
      // Same prompt, new version: refresh its row in place rather than listing it twice.
      setExperimentPrompts((rows) => [
        savedPrompt,
        ...rows.filter((row) => row.id !== savedPrompt.id),
      ]);
      // A save appended a version, so the history shown below is now stale.
      void loadHistory(savedPrompt.id);
      setExperimentPromptId((current) => current || savedPrompt.id);
      showToast(
        savedPrompt.version_number
          ? `Saved as version ${savedPrompt.version_number}.`
          : "Prompt saved to your workspace.",
      );
    } catch (error) {
      setSaved(false);
      setSavedVersion(null);
      setPreviewVersionId(null);
      setRestoreTarget(null);
      // Phase 3Q: a 404 on an update means the attached prompt was deleted elsewhere —
      // detach (state + stored identity) instead of leaving an attachment that can only
      // ever fail again. Never a replacement prompt.
      if (
        savedPromptId &&
        error instanceof ApiError &&
        error.status === 404
      ) {
        detachAttachment(
          "That saved prompt is no longer available. Start a new one when you're ready.",
        );
        return;
      }
      showToast(
        error instanceof Error ? error.message : "Could not save the prompt.",
        "error",
      );
    } finally {
      setSaving(false);
    }
  }

  // Phase 3J: load the saved prompt's version history, newest first for display.
  // The server returns ascending order; reversing is presentation only. "Current" is
  // the entry with the highest server-reported version_number — never array position.
  async function loadHistory(promptId: string) {
    setHistoryLoading(true);
    try {
      const versions = await api.getPromptVersions(promptId);
      setHistoryVersions([...versions].reverse());
      setPreviewVersionId(null);
    } catch {
      setHistoryVersions(null);
    } finally {
      setHistoryLoading(false);
    }
  }

  // Phase 3P: load the prompt's saved Evaluation History — bounded, newest first,
  // server-truth only. Reading history never evaluates anything: these are GETs.
  async function loadEvaluationHistory(promptId: string) {
    setEvalHistoryLoading(true);
    try {
      setEvalHistory(await api.getPromptEvaluations(promptId));
    } catch {
      setEvalHistory(null);
    } finally {
      setEvalHistoryLoading(false);
    }
  }

  // Phase 3P: open one stored evaluation's immutable detail (snapshots + verdicts
  // exactly as saved). Clicking the open row closes it; a failed load clears the
  // selection so the row can simply be retried.
  async function openEvaluationDetail(evaluationId: string) {
    if (evalDetailId === evaluationId) {
      setEvalDetailId(null);
      setEvalDetail(null);
      return;
    }
    setEvalDetailId(evaluationId);
    setEvalDetail(null);
    setEvalDetailLoading(true);
    try {
      setEvalDetail(await api.getEvaluation(evaluationId));
    } catch {
      setEvalDetailId(null);
      setEvalDetail(null);
      showToast("Could not load that evaluation.", "error");
    } finally {
      setEvalDetailLoading(false);
    }
  }

  // Phase 3R: put a stored evaluation into the existing compare slots. The record is
  // fetched once (reusing an already-open detail), mapped field-for-field by
  // historyToEvaluationResult, and compared in SUPPLIED mode — exactly what was
  // persisted. Clicking a row that already occupies a slot clears that slot; a row can
  // never land on both sides because historySelectionChoice only ever returns the slot
  // the row is not in.
  async function selectHistoryComparison(item: EvaluationHistoryItem) {
    const choice = historySelectionChoice(
      {
        left: compareLeft?.evaluation_id ?? null,
        right: compareRight?.evaluation_id ?? null,
      },
      item.evaluation_id,
    );
    if (choice.action === "clear") {
      setCompareSide(choice.side, null);
      return;
    }
    setCompareHistoryId(item.evaluation_id);
    try {
      const detail =
        evalDetailId === item.evaluation_id && evalDetail
          ? evalDetail
          : await api.getEvaluation(item.evaluation_id);
      setCompareSide(choice.side, historyToEvaluationResult(detail), "history");
      // Land the user on the existing compare area so the slots and result are visible.
      setCompareExpanded(true);
    } catch {
      showToast(
        "Could not load that evaluation for comparison.",
        "error",
      );
    } finally {
      setCompareHistoryId(null);
    }
  }

  // Phase 3J: restore appends a NEW version server-side. The editor adopts the newly
  // created version's body and savedVersion adopts its server-reported number — the
  // historical entry keeps its own number and is never relabeled current.
  async function confirmRestore() {
    if (!savedPromptId || !restoreTarget || restoring) return;
    setRestoring(true);
    try {
      const created = await api.restorePromptVersion(savedPromptId, restoreTarget.id);
      setPrompt(created.body);
      setSaved(true);
      setSavedVersion(created.version_number);
      setRestoreTarget(null);
      setPreviewVersionId(null);
      await loadHistory(savedPromptId);
      showToast(`Restored as version ${created.version_number}.`);
    } catch (error) {
      showToast(
        error instanceof Error ? error.message : "Could not restore the version.",
        "error",
      );
    } finally {
      setRestoring(false);
    }
  }

  async function enhanceWithAi() {
    if (!prompt || enhancing) return;
    if (!aiProvider) {
      showToast(
        "No AI provider is available. Configure one on the server first.",
        "error",
      );
      return;
    }
    setEnhancing(true);
    setEnhancement(null);
    try {
      const result = await api.enhancePrompt({
        prompt,
        provider: aiProvider,
        model: aiModel ?? undefined,
      });
      setEnhancement(result);
      showToast("Enhancement complete.");
    } catch (error) {
      setEnhancement(null);
      showToast(friendlyApiError(error), "error");
    } finally {
      setEnhancing(false);
    }
  }

  async function copyEnhanced() {
    if (!enhancement) return;
    const ok = await copyText(enhancement.enhanced_prompt);
    showToast(
      ok
        ? "Enhanced prompt copied to your clipboard."
        : "Copying is blocked in this browser. Select the text to copy it.",
      ok ? "success" : "error",
    );
  }

  function applyEnhancement() {
    if (!enhancement) return;
    setPrompt(enhancement.enhanced_prompt);
    setSaved(false);
    setSavedVersion(null);
    setPreviewVersionId(null);
    setRestoreTarget(null);
    showToast("Enhanced prompt applied to the editor above.");
  }

  async function createPromptWithAi() {
    const goal = createGoal.trim();
    if (!goal || creating) return;
    if (!aiProvider) {
      showToast(
        "No AI provider is available. Configure one on the server first.",
        "error",
      );
      return;
    }
    setCreating(true);
    setCreation(null);
    try {
      const result = await api.createPromptWithAI({
        goal,
        context: createContext.trim() || undefined,
        provider: aiProvider,
        model: aiModel ?? undefined,
      });
      setCreation(result);
      showToast("Prompt created.");
    } catch (error) {
      setCreation(null);
      showToast(friendlyApiError(error), "error");
    } finally {
      setCreating(false);
    }
  }

  async function copyCreated() {
    if (!creation) return;
    const ok = await copyText(creation.prompt);
    showToast(
      ok
        ? "Created prompt copied to your clipboard."
        : "Copying is blocked in this browser. Select the text to copy it.",
      ok ? "success" : "error",
    );
  }

  async function testPromptWithAi() {
    if (!prompt || testing) return;
    if (availableProviders.length === 0) {
      showToast(
        "No AI provider is available. Configure one on the server first.",
        "error",
      );
      return;
    }
    setTesting(true);
    setTestResult(null);
    try {
      // Phase 3O: when the editor shows exactly the current saved version, test
      // that version — the server executes its stored body and stamps the run
      // with the version identity. Any local edit makes it a draft, which tests
      // unpersisted exactly as before. Only the current (highest-numbered)
      // saved version qualifies; older-version bodies never match by position.
      let versionId: string | undefined;
      if (savedPromptId && historyVersions && historyVersions.length > 0) {
        const current = historyVersions.reduce((latest, item) =>
          item.version_number > latest.version_number ? item : latest,
        );
        if (current.body === prompt) versionId = current.id;
      }
      const result = await api.testPrompt({
        prompt,
        input: testInput.trim() || undefined,
        provider: aiProvider ?? undefined,
        model: aiModel ?? undefined,
        ...(savedPromptId && versionId
          ? { prompt_id: savedPromptId, version_id: versionId }
          : {}),
      });
      setTestResult(result);
      showToast("Prompt tested.");
    } catch (error) {
      setTestResult(null);
      showToast(friendlyApiError(error), "error");
    } finally {
      setTesting(false);
    }
  }

  async function copyTestOutput() {
    if (!testResult) return;
    const ok = await copyText(testResult.output);
    showToast(
      ok
        ? "Output copied to your clipboard."
        : "Copying is blocked in this browser. Select the text to copy it.",
      ok ? "success" : "error",
    );
  }

  function updateRule(id: string, patch: Partial<EvaluationRuleInput>) {
    setEvalRules((rules) =>
      rules.map((rule) => (rule.id === id ? { ...rule, ...patch } : rule)),
    );
  }

  function changeRuleType(id: string, type: EvaluationRuleType) {
    setEvalRules((rules) =>
      rules.map((rule) => {
        if (rule.id !== id) return rule;
        // Only the relevant parameter survives a type switch; the server re-validates.
        const next: StudioRule = {
          id: rule.id,
          label: rule.label,
          type,
        };
        if (CASE_SENSITIVE_RULE_TYPES.has(type))
          next.case_sensitive = rule.case_sensitive ?? true;
        return next;
      }),
    );
  }

  function removeRule(id: string) {
    setEvalRules((rules) =>
      rules.length > 1 ? rules.filter((rule) => rule.id !== id) : rules,
    );
  }

  function addRule() {
    setEvalRules((rules) => [
      ...rules,
      { id: newRuleId(), type: "contains", text: "", case_sensitive: true },
    ]);
  }

  // Shared build helpers: cleanedRules validates and normalizes the current rule set,
  // buildEvaluatorInput wraps it (plus the shared expected output) for evaluation or
  // run-pair comparison. Both keep the server authoritative for validation.
  function cleanedRules(): EvaluationRuleInput[] | null {
    const cleaned: EvaluationRuleInput[] = evalRules.map((rule) => ({
      ...rule,
      text: rule.text?.trim() || undefined,
      pattern: rule.pattern?.trim() || undefined,
      length: rule.length ?? undefined,
    }));
    // Client-side pre-check for a friendlier error; the server stays authoritative.
    for (const rule of cleaned) {
      if (TEXT_RULE_TYPES.has(rule.type) && !rule.text) {
        showToast(
          "Every contains / not contains rule needs text to search for.",
          "error",
        );
        return null;
      }
      if (LENGTH_RULE_TYPES.has(rule.type) && rule.length == null) {
        showToast("Every length rule needs a length.", "error");
        return null;
      }
      if (rule.type === "regex_match" && !rule.pattern) {
        showToast("The regex rule needs a pattern.", "error");
        return null;
      }
    }
    return cleaned;
  }

  function buildEvaluatorInput(
    cleaned: EvaluationRuleInput[],
  ): EvaluatorInput | null {
    const needsExpected = evalRules.some((rule) =>
      MATCH_RULE_TYPES.has(rule.type),
    );
    if (needsExpected && !evalExpected.trim()) {
      showToast(
        "Expected output is required for exact / normalized match rules.",
        "error",
      );
      return null;
    }
    const evaluator: EvaluatorInput = {
      name: "studio evaluation",
      rules: cleaned,
    };
    if (needsExpected) evaluator.expected_output = evalExpected.trim();
    return evaluator;
  }

  // Phase 3L: build the scoring profile from the selector + weight inputs.
  // Unweighted sends nothing (server default = Phase 3K). Weighted sends one
  // positive weight per rule id, in rule order. Client-side pre-checks mirror
  // cleanedRules: friendly errors here, authoritative validation server-side.
  // The frontend never computes the score — it only displays the response.
  function buildScoringInput(): ScoringInput | null {
    if (scoringMode === "unweighted") return null;
    const weights: RuleWeightInput[] = [];
    for (const rule of evalRules) {
      const raw = (ruleWeights[rule.id] ?? "1").trim();
      const value = raw === "" ? NaN : Number(raw);
      if (!Number.isFinite(value) || value <= 0 || value > 1000) {
        showToast(
          "Weights must be numbers above 0 (max 1000) — one per rule.",
          "error",
        );
        return null;
      }
      weights.push({ rule_id: rule.id, weight: value });
    }
    return { mode: "weighted", weights };
  }

  async function evaluatePromptWithAi() {
    if (!prompt || evaluating) return;
    const cleaned = cleanedRules();
    if (!cleaned) return;
    const evaluator = buildEvaluatorInput(cleaned);
    if (!evaluator) return;
    const scoring = buildScoringInput();
    if (scoringMode === "weighted" && !scoring) return;
    if (availableProviders.length === 0 && !testResult?.run_id) {
      showToast(
        "No AI provider is available to re-run the prompt. Run the Test first, then evaluate the persisted run.",
        "error",
      );
      return;
    }
    setEvaluating(true);
    setEvalResult(null);
    try {
      const input: EvaluationInput = testResult?.run_id
        ? { run_id: testResult.run_id, evaluator, ...(scoring ? { scoring } : {}) }
        : {
            execution: {
              prompt,
              input: testInput.trim() || undefined,
              provider: aiProvider ?? undefined,
              model: aiModel ?? undefined,
            },
            evaluator,
            ...(scoring ? { scoring } : {}),
          };
      const result = await api.evaluateEvaluation(input);
      setEvalResult(result);
      // Phase 3P: a run-anchored evaluation is saved server-side; show it in the
      // history list below. A draft evaluation (evaluation_id null) is not saved.
      if (savedPromptId) void loadEvaluationHistory(savedPromptId);
      showToast(
        result.passed
          ? "Evaluation passed all rules."
          : "Evaluation found failures.",
        result.passed ? "success" : "error",
      );
    } catch (error) {
      setEvalResult(null);
      showToast(friendlyApiError(error), "error");
    } finally {
      setEvaluating(false);
    }
  }

  function setCompareSide(
    side: "left" | "right",
    result: EvaluationResult | null,
    source: CompareSource = "capture",
  ) {
    if (side === "left") setCompareLeft(result);
    else setCompareRight(result);
    // Clearing a slot returns it to the default "capture" provenance; a filled slot
    // remembers where it came from so comparePromptResults can pick the right mode.
    setCompareSources((current) => ({ ...current, [side]: result ? source : "capture" }));
    setComparison(null);
  }

  async function comparePromptResults() {
    if (!compareLeft || !compareRight || comparing) return;
    setComparing(true);
    setComparison(null);
    try {
      const input: ComparisonInput = {};
      // Phase 3R: a slot holding a stored history record is ALWAYS supplied mode —
      // compare the records exactly as they were persisted. Run-pair mode would
      // re-evaluate both runs with the rules currently in the editor instead.
      const fromHistory =
        compareSources.left === "history" || compareSources.right === "history";
      // Run-pair mode: both captured sides are persisted runs, so they are
      // re-evaluated server-side with the CURRENT rules (no provider contact).
      if (!fromHistory && compareLeft.run_id && compareRight.run_id) {
        const cleaned = cleanedRules();
        if (!cleaned) return;
        const evaluator = buildEvaluatorInput(cleaned);
        if (!evaluator) return;
        input.left_run_id = compareLeft.run_id;
        input.right_run_id = compareRight.run_id;
        input.evaluator = evaluator;
      } else {
        // Supplied mode: compare the captured results exactly as they were.
        input.left_evaluation = compareLeft;
        input.right_evaluation = compareRight;
      }
      const result = await api.compareEvaluation(input);
      setComparison(result);
      showToast(
        "Comparison complete — the differences are shown below.",
        "success",
      );
    } catch (error) {
      showToast(friendlyApiError(error), "error");
    } finally {
      setComparing(false);
    }
  }

  // Phase 3G: apply ONE evaluator to MANY targets — the persisted test run (when
  // captured) plus any fresh execution rows below — and report integer counts
  // only (how many of the targets passed). No scores, no ratios, no ranking.
  async function runEvaluationSuite() {
    if (!prompt || suiteRunning) return;
    const cleaned = cleanedRules();
    if (!cleaned) return;
    const evaluator = buildEvaluatorInput(cleaned);
    if (!evaluator) return;

    const targets: EvaluationSuiteTarget[] = [];
    // MODE A: the persisted test run is a target whenever one was captured.
    if (testResult?.run_id) {
      targets.push({ run_id: testResult.run_id });
    }
    // MODE B: every non-blank execution row becomes a fresh-execution target,
    // reusing the shared test input and the selected provider/model.
    for (const row of suiteTargets) {
      if (row.trim()) {
        targets.push({
          execution: {
            prompt: row.trim(),
            input: testInput,
            provider: aiProvider ?? undefined,
            model: aiModel ?? undefined,
          },
        });
      }
    }
    if (targets.length === 0) {
      showToast(
        "Add at least one target — run the prompt on the left, or add an execution row below.",
        "error",
      );
      return;
    }
    if (targets.length > 20) {
      showToast("An evaluation suite supports at most 20 targets.", "error");
      return;
    }

    setSuiteRunning(true);
    setSuiteResult(null);
    try {
      const result = await api.runEvaluationSuite({ evaluator, targets });
      setSuiteResult(result);
      showToast(
        `Evaluation suite complete — ${result.passed} of ${result.total} targets passed.`,
        result.passed === result.total ? "success" : "info",
      );
    } catch (error) {
      showToast(friendlyApiError(error), "error");
    } finally {
      setSuiteRunning(false);
    }
  }

  // Phase 3H: apply ONE evaluator to EVERY saved version of ONE owned prompt. The
  // version axis is resolved server-side, so nothing about it is sent or ordered here
  // — the response comes back oldest-to-newest and is reported as integer counts only
  // (how many versions passed). No scores, no ratios, no ranking, no verdict.
  async function runPromptExperiment() {
    if (experimentRunning) return;
    const promptId = experimentPromptId || savedPromptId;
    if (!promptId) {
      showToast(
        "Save a prompt to your workspace first — an experiment measures saved versions.",
        "error",
      );
      return;
    }
    const cleaned = cleanedRules();
    if (!cleaned) return;
    const evaluator = buildEvaluatorInput(cleaned);
    if (!evaluator) return;

    setExperimentRunning(true);
    setExperimentResult(null);
    try {
      const result = await api.runExperiment({ prompt_id: promptId, evaluator });
      setExperimentResult(result);
      showToast(
        `Experiment complete — ${result.passed} of ${result.total_versions} versions passed.`,
        result.total_versions > 0 && result.passed === result.total_versions
          ? "success"
          : "info",
      );
    } catch (error) {
      showToast(friendlyApiError(error), "error");
    } finally {
      setExperimentRunning(false);
    }
  }

  async function analyzeWithAi() {
    if (!prompt || analyzing) return;
    if (availableProviders.length === 0) {
      showToast(
        "No AI provider is available. Configure one on the server first.",
        "error",
      );
      return;
    }
    setAnalyzing(true);
    setAnalysis(null);
    try {
      const result = await api.analyzePrompt({
        prompt,
        provider: aiProvider ?? undefined,
        model: aiModel ?? undefined,
      });
      setAnalysis(result);
      showToast("Analysis complete.");
    } catch (error) {
      setAnalysis(null);
      showToast(friendlyApiError(error), "error");
    } finally {
      setAnalyzing(false);
    }
  }

  const html = useMemo(
    () => (prompt ? renderPromptHtml(prompt) : ""),
    [prompt],
  );

  // Phase 3Q: while an attach is in flight the server response has not arrived, so the
  // editor must not render a blank "new prompt" that could be mistaken for (or saved
  // over) the prompt being opened. Existing skeleton components, no new loading
  // framework — and because every hook above has already run, this is a plain render
  // branch rather than an early bail-out of the component.
  if (attaching) {
    return (
      <section className="mx-auto grid max-w-[1400px] items-start gap-6 px-6 pt-2 pb-16 sm:px-8 lg:grid-cols-[1fr_1.1fr]">
        <div
          className="rounded-2xl border border-border bg-card p-6 lg:col-span-2"
          aria-busy="true"
        >
          <p className="flex items-center gap-2 text-[12px] font-semibold text-muted">
            <Icon name="clock" size={14} />
            Opening saved prompt…
          </p>
          <div className="mt-4">
            <CardSkeleton count={2} />
          </div>
        </div>
      </section>
    );
  }

  return (
    <section className="mx-auto grid max-w-[1400px] items-start gap-6 px-6 pt-2 pb-16 sm:px-8 lg:grid-cols-[1fr_1.1fr]">
      {/* Input column */}
      <Card className="p-6">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2.5">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-purple-soft font-mono text-[11px] font-bold text-purple">
              01
            </span>
            <h2 className="text-sm font-bold text-ink">
              What do you want to create?
            </h2>
          </div>
          <span className="text-[11px] text-faint">Your rough idea</span>
        </div>

        <div className="relative mt-4">
          <Textarea
            maxLength={1200}
            rows={6}
            value={idea}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter")
                generate();
            }}
            placeholder="Example: Build a landing page for a new coffee subscription…"
            className="min-h-[150px] pb-8 text-[13px]"
            onChange={(event) => {
              setIdea(event.target.value);
              setSaved(false);
              setSavedVersion(null);
              setPreviewVersionId(null);
              setRestoreTarget(null);
            }}
          />
          <span className="absolute right-3 bottom-3 font-mono text-[10px] text-faint">
            <b className="font-medium text-muted">{idea.length}</b>/1200
          </span>
        </div>

        <div className="mt-3 flex flex-wrap items-center gap-2 text-[12px] text-faint">
          <span>Try a spark:</span>
          {examples.map((example) => (
            <button
              key={example.label}
              type="button"
              onClick={() => setIdea(example.idea)}
              className="rounded-md border border-border bg-paper px-2 py-1 text-[11px] font-semibold text-muted transition hover:border-purple hover:text-purple dark:bg-raised"
            >
              {example.label}
            </button>
          ))}
        </div>

        <div className="mt-5 grid grid-cols-2 gap-4 border-t border-border pt-5 max-[520px]:grid-cols-1">
          <label className="block text-[11px] font-bold text-ink-soft">
            I am a…
            <Select
              value={audience}
              onChange={(event) => setAudience(event.target.value as Audience)}
              className="mt-1.5"
              aria-label="Audience or role"
            >
              <option value="everyone">Curious human</option>
              <option value="developer">Developer</option>
              <option value="creator">Creator / designer</option>
              <option value="business">Business builder</option>
              <option value="student">Student / researcher</option>
            </Select>
          </label>
          <label className="block text-[11px] font-bold text-ink-soft">
            I want the output to be…
            <Select
              value={output}
              onChange={(event) =>
                setOutput(event.target.value as OutputFormat)
              }
              className="mt-1.5"
              aria-label="Output format"
            >
              <option value="best">Best format for the task</option>
              <option value="steps">Step-by-step</option>
              <option value="detailed">Detailed and thorough</option>
              <option value="concise">Clear and concise</option>
              <option value="table">Organized as a table</option>
              <option value="code">Production-ready code</option>
            </Select>
          </label>
          <label className="col-span-2 flex flex-col text-[11px] font-bold text-ink-soft">
            <span className="flex justify-between">
              Depth <Badge tone="purple">{depthGuidance[depth].label}</Badge>
            </span>
            <input
              type="range"
              min={1}
              max={3}
              value={depth}
              onChange={(event) =>
                setDepth(Number(event.target.value) as DepthLevel)
              }
              className="mt-2 w-full accent-purple"
              aria-label="Prompt depth"
            />
            <span className="flex justify-between text-[10px] text-faint">
              <span>Quick</span>
              <span>Balanced</span>
              <span>Deep</span>
            </span>
          </label>
        </div>

        <Button
          variant="primary"
          size="lg"
          icon="spark"
          onClick={() => generate()}
          disabled={busy || !idea.trim()}
          className="mt-5 w-full"
        >
          {busy ? "Working…" : "Generate prompt"}
          <kbd className="ml-auto text-[#d7ceff]">⌘ ↵</kbd>
        </Button>
        <p className="mt-3 text-center text-[11px] text-faint">
          Local enhancer is the offline fallback — AI enhancement below is
          optional.
        </p>

        <div className="mt-5 border-t border-border pt-5">
          <div className="flex items-center gap-2">
            <Icon name="server" size={14} className="text-purple" />
            <h3 className="text-[11px] font-bold tracking-wide text-ink-soft uppercase">
              Enhance with AI
            </h3>
          </div>
          <p className="mt-1 text-[11px] text-faint">
            Sends your draft above through the Phase 2 gateway and returns a
            rewritten prompt plus a record of what changed.
          </p>
          <div className="mt-3 grid grid-cols-2 gap-3 max-[520px]:grid-cols-1">
            <label className="block text-[11px] font-bold text-ink-soft">
              Provider
              <Select
                value={aiProvider ?? ""}
                onChange={(event) => selectProvider(event.target.value)}
                disabled={availableProviders.length === 0}
                className="mt-1.5"
                aria-label="AI provider"
              >
                {availableProviders.length === 0 ? (
                  <option value="">No provider available</option>
                ) : (
                  availableProviders.map((provider) => (
                    <option key={provider.id} value={provider.id}>
                      {provider.name}
                    </option>
                  ))
                )}
              </Select>
            </label>
            <label className="block text-[11px] font-bold text-ink-soft">
              Model
              <Select
                value={aiModel ?? ""}
                onChange={(event) => setAiModel(event.target.value || null)}
                disabled={availableProviders.length === 0}
                className="mt-1.5"
                aria-label="AI model"
              >
                {selectedModels.length === 0 ? (
                  <option value="">Provider default</option>
                ) : (
                  selectedModels.map((model) => (
                    <option key={model} value={model}>
                      {model}
                    </option>
                  ))
                )}
              </Select>
            </label>
          </div>
          <Button
            variant="secondary"
            size="lg"
            icon="spark"
            onClick={() => void enhanceWithAi()}
            disabled={!ready || enhancing || availableProviders.length === 0}
            className="mt-4 w-full"
          >
            {enhancing ? "Enhancing…" : "Enhance with AI"}
          </Button>
          <p className="mt-2 text-center text-[11px] text-faint">
            {providers.length === 0
              ? "No AI providers reported yet — start FastAPI and refresh."
              : `${availableProviders.length} of ${providers.length} providers available.`}
          </p>
          {enhancement && (
            <div className="mt-4 space-y-3">
              <div>
                <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                  Your original draft
                </p>
                <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
                  {enhancement.original_prompt}
                </p>
              </div>
              <div>
                <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                  Enhanced prompt
                </p>
                <div className="mt-1 max-h-56 overflow-auto rounded-lg border border-border bg-paper p-3 text-[12px] leading-relaxed whitespace-pre-wrap text-ink-soft dark:bg-raised">
                  {enhancement.enhanced_prompt}
                </div>
                <div className="mt-2 grid grid-cols-2 gap-2 max-[520px]:grid-cols-1">
                  <Button
                    variant="secondary"
                    icon="copy"
                    onClick={() => void copyEnhanced()}
                  >
                    Copy
                  </Button>
                  <Button
                    variant="secondary"
                    icon="refresh"
                    onClick={applyEnhancement}
                  >
                    Apply to editor
                  </Button>
                </div>
              </div>
              <AnalysisList
                title="What changed"
                items={enhancement.improvements}
              />
            </div>
          )}
        </div>

        <div className="mt-5 border-t border-border pt-5">
          <div className="flex items-center gap-2">
            <Icon name="layers" size={14} className="text-purple" />
            <h3 className="text-[11px] font-bold tracking-wide text-ink-soft uppercase">
              Analyze your prompt with AI
            </h3>
          </div>
          <p className="mt-1 text-[11px] text-faint">
            Sends the draft above to the same Phase 2 gateway and returns a
            structured critique. This is AI analysis — it does not rewrite or
            enhance the prompt.
          </p>
          <Button
            variant="secondary"
            size="lg"
            icon="info"
            onClick={() => void analyzeWithAi()}
            disabled={!ready || analyzing || availableProviders.length === 0}
            className="mt-3 w-full"
          >
            {analyzing ? "Analyzing…" : "Analyze Prompt"}
          </Button>
          <p className="mt-2 text-center text-[11px] text-faint">
            {availableProviders.length === 0
              ? "No AI provider is available, so analysis is unavailable until one is configured."
              : "Analysis runs on the selected provider and model above."}
          </p>
          {analysis && (
            <div className="mt-4 space-y-3">
              {(
                [
                  ["Clarity", analysis.clarity],
                  ["Specificity", analysis.specificity],
                  ["Context", analysis.context],
                  ["Constraints", analysis.constraints],
                  ["Output format", analysis.output_format],
                ] as const
              ).map(([label, value]) => (
                <div key={label}>
                  <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                    {label}
                  </p>
                  <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
                    {value ?? "—"}
                  </p>
                </div>
              ))}
              <AnalysisList
                title="Missing information"
                items={analysis.missing_information}
              />
              <AnalysisList title="Suggestions" items={analysis.suggestions} />
            </div>
          )}
        </div>

        <div className="mt-5 border-t border-border pt-5">
          <div className="flex items-center gap-2">
            <Icon name="server" size={14} className="text-purple" />
            <h3 className="text-[11px] font-bold tracking-wide text-ink-soft uppercase">
              Create a prompt with AI
            </h3>
          </div>
          <p className="mt-1 text-[11px] text-faint">
            Describe a goal and get a complete, reusable prompt. Optional
            context (audience, platform, constraints) makes the draft more
            precise.
          </p>
          <div className="relative mt-3">
            <Textarea
              maxLength={2000}
              rows={3}
              value={createGoal}
              placeholder="Example: A tool that turns rough ideas into ready-to-use prompts…"
              className="text-[12px]"
              onChange={(event) => setCreateGoal(event.target.value)}
            />
            <span className="absolute right-3 bottom-3 font-mono text-[10px] text-faint">
              <b className="font-medium text-muted">{createGoal.length}</b>/2000
            </span>
          </div>
          <div className="relative mt-3">
            <Textarea
              maxLength={2000}
              rows={2}
              value={createContext}
              placeholder="Optional context: who it is for, where it runs, what to avoid…"
              className="text-[12px]"
              aria-label="Optional context for the created prompt"
              onChange={(event) => setCreateContext(event.target.value)}
            />
          </div>
          <Button
            variant="secondary"
            size="lg"
            icon="spark"
            onClick={() => void createPromptWithAi()}
            disabled={
              !createGoal.trim() || creating || availableProviders.length === 0
            }
            className="mt-4 w-full"
          >
            {creating ? "Creating…" : "Create prompt"}
          </Button>
          <p className="mt-2 text-center text-[11px] text-faint">
            {availableProviders.length === 0
              ? "No AI provider is available, so prompt creation is unavailable until one is configured."
              : "Creation runs on the selected provider and model above."}
          </p>
          {creation && (
            <div className="mt-4 space-y-3">
              <div>
                <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                  Generated prompt
                </p>
                <div className="mt-1 max-h-56 overflow-auto rounded-lg border border-border bg-paper p-3 text-[12px] leading-relaxed whitespace-pre-wrap text-ink-soft dark:bg-raised">
                  {creation.prompt}
                </div>
                <Button
                  variant="secondary"
                  icon="copy"
                  onClick={() => void copyCreated()}
                  className="mt-2 w-full"
                >
                  Copy prompt
                </Button>
              </div>
              <div>
                <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                  Why this design
                </p>
                <p className="mt-0.5 text-[12px] leading-relaxed text-muted">
                  {creation.rationale}
                </p>
              </div>
            </div>
          )}
        </div>

        <div className="mt-5 border-t border-border pt-5">
          <div className="flex items-center gap-2">
            <Icon name="spark" size={14} className="text-purple" />
            <h3 className="text-[11px] font-bold tracking-wide text-ink-soft uppercase">
              Test your prompt
            </h3>
          </div>
          <p className="mt-1 text-[11px] text-faint">
            Executes the draft above through the same Phase 2 gateway so you can
            see the actual response, provider, model, and latency. Optional test
            input is appended as labeled input.
          </p>
          <div className="relative mt-3">
            <Textarea
              maxLength={2000}
              rows={2}
              value={testInput}
              placeholder="Optional test input, e.g. Product: wireless headphones…"
              className="text-[12px]"
              aria-label="Optional test input"
              onChange={(event) => setTestInput(event.target.value)}
            />
          </div>
          <Button
            variant="secondary"
            size="lg"
            icon="spark"
            onClick={() => void testPromptWithAi()}
            disabled={!ready || testing || availableProviders.length === 0}
            className="mt-4 w-full"
          >
            {testing ? "Testing…" : "Test Prompt"}
          </Button>
          <p className="mt-2 text-center text-[11px] text-faint">
            {availableProviders.length === 0
              ? "No AI provider is available, so testing is unavailable until one is configured."
              : "Testing runs on the selected provider and model above."}
          </p>
          {testResult && (
            <div className="mt-4 space-y-3">
              <div>
                <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                  Generated output
                </p>
                <div className="mt-1 max-h-56 overflow-auto rounded-lg border border-border bg-paper p-3 text-[12px] leading-relaxed whitespace-pre-wrap text-ink-soft dark:bg-raised">
                  {testResult.output}
                </div>
                <Button
                  variant="secondary"
                  icon="copy"
                  onClick={() => void copyTestOutput()}
                  className="mt-2 w-full"
                >
                  Copy output
                </Button>
              </div>
              <div className="grid grid-cols-2 gap-x-3 gap-y-2 max-[520px]:grid-cols-1">
                <div>
                  <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                    Provider / Model
                  </p>
                  <p
                    className="mt-0.5 truncate text-[11px] text-muted"
                    title={`${testResult.provider} / ${testResult.model}`}
                  >
                    {testResult.provider} / {testResult.model}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                    Finish
                  </p>
                  <p className="mt-0.5 text-[11px] text-muted">
                    {testResult.finish_reason ?? "—"}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                    Latency
                  </p>
                  <p className="mt-0.5 text-[11px] text-muted">
                    {testResult.latency_ms != null
                      ? `${testResult.latency_ms} ms`
                      : "—"}
                  </p>
                </div>
                <div>
                  <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                    Tokens
                  </p>
                  <p className="mt-0.5 text-[11px] text-muted">
                    {testResult.usage
                      ? `${testResult.usage.prompt_tokens ?? "?"} in / ${testResult.usage.completion_tokens ?? "?"} out`
                      : "Not reported"}
                  </p>
                </div>
              </div>
            </div>
          )}

          {/* Phase 3E: deterministic evaluation (collapsed until the user opens it) */}
          <div className="mt-5 border-t border-border pt-5">
            <div className="flex items-center gap-2">
              <Icon name="check" size={14} className="text-purple" />
              <h3 className="text-[11px] font-bold tracking-wide text-ink-soft uppercase">
                Evaluate your prompt
              </h3>
            </div>
            <p className="mt-1 text-[11px] text-faint">
              Applies your deterministic rules to the tested output — or re-runs
              the draft above if nothing was tested yet — and returns a
              PASS/FAIL verdict per rule plus a deterministic score. No LLM
              judge; the rules are the only authority.
            </p>
            {!evalExpanded ? (
              <Button
                variant="secondary"
                size="lg"
                icon="check"
                onClick={() => {
                  setEvalExpanded(true);
                  // Phase 3P: surface saved history as soon as the lane opens.
                  if (savedPromptId) void loadEvaluationHistory(savedPromptId);
                }}
                className="mt-3 w-full"
              >
                Build evaluation rules
              </Button>
            ) : (
              <>
                <div className="mt-3 space-y-2">
                  {evalRules.map((rule, index) => {
                    const isMatch = MATCH_RULE_TYPES.has(rule.type);
                    const isLength = LENGTH_RULE_TYPES.has(rule.type);
                    const isRegex = rule.type === "regex_match";
                    const isText = TEXT_RULE_TYPES.has(rule.type);
                    const showCase = CASE_SENSITIVE_RULE_TYPES.has(rule.type);
                    return (
                      <div
                        key={rule.id}
                        className="rounded-lg border border-border bg-paper p-2.5 dark:bg-raised"
                      >
                        <div className="flex items-center gap-2">
                          <Select
                            value={rule.type}
                            onChange={(event) =>
                              changeRuleType(
                                rule.id,
                                event.target.value as EvaluationRuleType,
                              )
                            }
                            className="h-8 flex-1 text-[12px]"
                            aria-label={`Rule ${index + 1} type`}
                          >
                            {RULE_TYPES.map((option) => (
                              <option key={option.value} value={option.value}>
                                {option.label}
                              </option>
                            ))}
                          </Select>
                          <button
                            type="button"
                            onClick={() => removeRule(rule.id)}
                            disabled={evalRules.length === 1}
                            className="grid h-7 w-7 shrink-0 place-items-center rounded-md text-muted transition hover:bg-card hover:text-danger disabled:opacity-40"
                            title="Remove rule"
                            aria-label={`Remove rule ${index + 1}`}
                          >
                            <Icon name="x" size={14} />
                          </button>
                        </div>
                        {isText && (
                          <Input
                            value={rule.text ?? ""}
                            maxLength={5000}
                            placeholder="Text to search for…"
                            className="mt-2 h-8 text-[12px]"
                            aria-label={`Text to search for in rule ${index + 1}`}
                            onChange={(event) =>
                              updateRule(rule.id, { text: event.target.value })
                            }
                          />
                        )}
                        {isLength && (
                          <Input
                            type="number"
                            min={1}
                            max={1000000}
                            value={rule.length ?? ""}
                            placeholder="Length"
                            className="mt-2 h-8 w-32 text-[12px]"
                            aria-label={`Length for rule ${index + 1}`}
                            onChange={(event) =>
                              updateRule(rule.id, {
                                length:
                                  event.target.value === ""
                                    ? undefined
                                    : Number(event.target.value),
                              })
                            }
                          />
                        )}
                        {isRegex && (
                          <Input
                            value={rule.pattern ?? ""}
                            maxLength={500}
                            placeholder="Regex pattern, e.g. \\bpython\\b"
                            className="mt-2 h-8 font-mono text-[12px]"
                            aria-label={`Regex pattern for rule ${index + 1}`}
                            onChange={(event) =>
                              updateRule(rule.id, {
                                pattern: event.target.value,
                              })
                            }
                          />
                        )}
                        {isLength && (
                          <label className="mt-2 flex items-center gap-1.5 text-[11px] text-muted">
                            <input
                              type="checkbox"
                              checked={rule.strip === true}
                              onChange={(event) =>
                                updateRule(rule.id, {
                                  strip: event.target.checked,
                                })
                              }
                              className="accent-purple"
                            />
                            Trim whitespace before measuring
                          </label>
                        )}
                        {showCase && (
                          <label className="mt-2 flex items-center gap-1.5 text-[11px] text-muted">
                            <input
                              type="checkbox"
                              checked={rule.case_sensitive !== false}
                              onChange={(event) =>
                                updateRule(rule.id, {
                                  case_sensitive: event.target.checked,
                                })
                              }
                              className="accent-purple"
                            />
                            Case-sensitive
                          </label>
                        )}
                        {isMatch && (
                          <p className="mt-2 text-[11px] text-faint">
                            Uses the shared expected output below.
                          </p>
                        )}
                      </div>
                    );
                  })}
                </div>
                <Button
                  variant="ghost"
                  size="sm"
                  icon="plus"
                  onClick={addRule}
                  disabled={evalRules.length >= 20}
                  className="mt-2"
                >
                  Add rule
                </Button>
                {evalRules.some((rule) => MATCH_RULE_TYPES.has(rule.type)) && (
                  <div className="mt-3">
                    <label className="block text-[11px] font-bold text-ink-soft">
                      Expected output (required for exact / normalized match)
                    </label>
                    <Textarea
                      maxLength={20000}
                      rows={3}
                      value={evalExpected}
                      placeholder="The output you expect the prompt to produce…"
                      className="mt-1.5 text-[12px]"
                      aria-label="Expected output for match rules"
                      onChange={(event) => setEvalExpected(event.target.value)}
                    />
                  </div>
                )}
                <div className="mt-3">
                  <p className="text-[11px] font-bold text-ink-soft">Scoring</p>
                  <div className="mt-1.5 flex gap-4 text-[12px] text-muted">
                    <label className="flex items-center gap-1.5">
                      <input
                        type="radio"
                        name="scoring-mode"
                        checked={scoringMode === "unweighted"}
                        onChange={() => setScoringMode("unweighted")}
                        className="accent-purple"
                      />
                      Equal criteria
                    </label>
                    <label className="flex items-center gap-1.5">
                      <input
                        type="radio"
                        name="scoring-mode"
                        checked={scoringMode === "weighted"}
                        onChange={() => setScoringMode("weighted")}
                        className="accent-purple"
                      />
                      Weighted criteria
                    </label>
                  </div>
                  {scoringMode === "weighted" && (
                    <ul className="mt-2 space-y-1.5">
                      {evalRules.map((rule, index) => (
                        <li
                          key={rule.id}
                          className="flex items-center gap-2 rounded-lg border border-border bg-paper p-2 dark:bg-raised"
                        >
                          <p className="min-w-0 flex-1 truncate text-[12px] text-ink-soft">
                            {ruleLabel(rule.type)}
                            {rule.text ? ` “${rule.text.slice(0, 40)}”` : ""}
                            {rule.length != null ? ` · ${rule.length}` : ""}
                            {rule.pattern
                              ? ` · /${rule.pattern.slice(0, 24)}/`
                              : ""}
                          </p>
                          <label className="flex shrink-0 items-center gap-1.5 text-[11px] text-muted">
                            Weight
                            <Input
                              type="number"
                              min={0}
                              max={1000}
                              step="any"
                              value={ruleWeights[rule.id] ?? "1"}
                              className="h-8 w-24 text-[12px]"
                              aria-label={`Weight for rule ${index + 1}`}
                              onChange={(event) =>
                                setRuleWeights((current) => ({
                                  ...current,
                                  [rule.id]: event.target.value,
                                }))
                              }
                            />
                          </label>
                        </li>
                      ))}
                    </ul>
                  )}
                  <p className="mt-1.5 text-[11px] text-faint">
                    {scoringMode === "unweighted"
                      ? "Each passing rule counts the same."
                      : "Each rule contributes its weight to the score. Weights must be above 0."}
                  </p>
                </div>
                <Button
                  variant="primary"
                  size="lg"
                  icon="check"
                  onClick={() => void evaluatePromptWithAi()}
                  disabled={
                    !ready ||
                    evaluating ||
                    (availableProviders.length === 0 && !testResult?.run_id)
                  }
                  className="mt-3 w-full"
                >
                  {evaluating ? "Evaluating…" : "Run evaluation"}
                </Button>
                <p className="mt-2 text-center text-[11px] text-faint">
                  {evalResult
                    ? evalResult.run_id
                      ? "Evaluated the persisted test run."
                      : "Re-ran the prompt for this evaluation."
                    : "Evaluates the output against your rules — deterministic only, up to 20 rules."}
                </p>
                {evalResult && (
                  <div className="mt-4 space-y-3">
                    <div className="flex items-center gap-2">
                      <Badge tone={evalResult.passed ? "success" : "danger"}>
                        {evalResult.passed ? "PASS" : "FAIL"}
                      </Badge>
                      <span className="text-[11px] text-faint">
                        {
                          evalResult.verdicts.filter(
                            (verdict) => verdict.passed,
                          ).length
                        }
                        /{evalResult.verdicts.length} criteria passed
                      </span>
                      {/* Phase 3K: deterministic criteria-passed percentage, server-reported.
                          Factual metadata only — never a quality label or ranking. */}
                      <span className="text-[11px] text-faint">
                        Score{" "}
                        {evalResult.score == null ? "—" : `${evalResult.score}%`}
                      </span>
                      {/* Phase 3P: durable history id when this evaluation was saved
                          (null = draft execution with no stored run, so nothing saved). */}
                      <span
                        className="text-[11px] text-faint"
                        title={evalResult.evaluation_id ?? undefined}
                      >
                        {evalResult.evaluation_id
                          ? `Saved · ${evalResult.evaluation_id.slice(0, 8)}`
                          : "Not saved"}
                      </span>
                    </div>
                    <ul className="space-y-1.5">
                      {evalResult.verdicts.map((verdict, index) => (
                        <li
                          key={verdict.rule_id ?? index}
                          className="flex items-start gap-2 rounded-lg border border-border bg-paper p-2 text-[12px] dark:bg-raised"
                        >
                          <span
                            className={`mt-0.5 ${verdict.passed ? "text-success" : "text-danger"}`}
                          >
                            <Icon
                              name={verdict.passed ? "check" : "x"}
                              size={14}
                            />
                          </span>
                          <div className="min-w-0 flex-1">
                            <p className="font-semibold text-ink-soft">
                              {verdict.label ?? ruleLabel(verdict.type)}
                            </p>
                            <p className="text-[11px] text-muted">
                              {evidenceSummary(verdict.evidence)}
                            </p>
                          </div>
                          <Badge tone={verdict.passed ? "success" : "danger"}>
                            {verdict.passed ? "PASS" : "FAIL"}
                          </Badge>
                        </li>
                      ))}
                    </ul>
                    <div>
                      <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
                        Evaluated output
                      </p>
                      <div className="mt-1 max-h-40 overflow-auto rounded-lg border border-border bg-paper p-3 text-[12px] leading-relaxed whitespace-pre-wrap text-ink-soft dark:bg-raised">
                        {evalResult.output}
                      </div>
                    </div>
                    <p className="text-[11px] text-faint">
                      {evalResult.provider} / {evalResult.model} ·{" "}
                      {evalResult.latency_ms != null
                        ? `${evalResult.latency_ms} ms`
                        : "latency n/a"}{" "}
                      ·{" "}
                      {evalResult.usage
                        ? `${evalResult.usage.prompt_tokens ?? "?"} in / ${evalResult.usage.completion_tokens ?? "?"} out`
                        : "tokens not reported"}
                    </p>
                    <div className="mt-1 flex flex-wrap items-center gap-2">
                      <Button
                        variant={compareLeft === evalResult ? "secondary" : "ghost"}
                        size="sm"
                        onClick={() => setCompareSide("left", evalResult)}
                        className="flex-1"
                      >
                        {compareLeft === evalResult
                          ? "Left slot: captured"
                          : "Capture as left"}
                      </Button>
                      <Button
                        variant={compareRight === evalResult ? "secondary" : "ghost"}
                        size="sm"
                        onClick={() => setCompareSide("right", evalResult)}
                        className="flex-1"
                      >
                        {compareRight === evalResult
                          ? "Right slot: captured"
                          : "Capture as right"}
                      </Button>
                    </div>
                  </div>
                )}

                {/* Phase 3P: durable Evaluation History — one row per saved evaluation,
                    newest first, server truth only. Read-only by design: the API is
                    append-only, so there is no edit, no delete, and opening a row never
                    re-evaluates anything. */}
                <div className="mt-5 border-t border-border pt-5">
                  <div className="flex items-center justify-between gap-2">
                    <p className="text-[10px] font-semibold tracking-wide text-faint uppercase">
                      Evaluation history
                      {evalHistory ? ` · ${evalHistory.length}` : ""}
                    </p>
                    <Button
                      variant="ghost"
                      size="sm"
                      icon="refresh"
                      disabled={!savedPromptId || evalHistoryLoading}
                      onClick={() =>
                        savedPromptId && void loadEvaluationHistory(savedPromptId)
                      }
                    >
                      {evalHistoryLoading ? "Loading…" : "Refresh"}
                    </Button>
                  </div>
                  {!savedPromptId ? (
                    <p className="mt-2 text-[11px] text-muted">
                      Save the prompt first — only evaluations anchored to a stored run
                      are kept.
                    </p>
                  ) : evalHistoryLoading && !evalHistory ? (
                    <p className="mt-2 text-[11px] text-muted">Loading history…</p>
                  ) : !evalHistory || evalHistory.length === 0 ? (
                    <p className="mt-2 text-[11px] text-muted">
                      No saved evaluations yet.
                    </p>
                  ) : (
                    <ul className="mt-2 space-y-1.5">
                      {evalHistory.map((item) => {
                        // Phase 3R: which slot (if any) already holds this row.
                        const selectedSide =
                          compareLeft?.evaluation_id === item.evaluation_id
                            ? "left"
                            : compareRight?.evaluation_id === item.evaluation_id
                              ? "right"
                              : null;
                        return (
                        <li
                          key={item.evaluation_id}
                          className="rounded-lg border border-border bg-paper p-2 dark:bg-raised"
                        >
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <div className="flex min-w-0 flex-wrap items-center gap-2 text-[12px] text-ink-soft">
                              {/* Version, score, mode, and date are exactly what the
                                  server stored for this row — never recomputed here. */}
                              <span className="font-mono font-bold">
                                {item.version_number
                                  ? `v${item.version_number}`
                                  : "unversioned"}
                              </span>
                              <Badge tone={item.passed ? "success" : "danger"}>
                                {item.passed ? "PASS" : "FAIL"}
                              </Badge>
                              <span className="text-faint">
                                Score {item.score == null ? "—" : `${item.score}%`}
                              </span>
                              <span className="text-faint">
                                {item.scoring_mode === "weighted"
                                  ? "Weighted criteria"
                                  : "Equal criteria"}
                              </span>
                              <span className="text-faint">
                                {new Date(item.created_at).toLocaleString()}
                              </span>
                            </div>
                            <div className="flex shrink-0 items-center gap-1.5">
                              {/* Phase 3R: the only comparison affordance on a row. One
                                  button per row means one evaluation can never occupy
                                  both slots; activating it again clears that slot. */}
                              <Button
                                variant={selectedSide ? "secondary" : "ghost"}
                                size="sm"
                                icon="layers"
                                title="Select for comparison"
                                disabled={
                                  compareHistoryId === item.evaluation_id
                                }
                                onClick={() =>
                                  void selectHistoryComparison(item)
                                }
                              >
                                {compareHistoryId === item.evaluation_id
                                  ? "Loading…"
                                  : selectedSide === "left"
                                    ? "Selected: left"
                                    : selectedSide === "right"
                                      ? "Selected: right"
                                      : "Select for comparison"}
                              </Button>
                              <Button
                                variant="ghost"
                                size="sm"
                                icon="eye"
                                onClick={() =>
                                  void openEvaluationDetail(item.evaluation_id)
                                }
                              >
                                {evalDetailId === item.evaluation_id ? "Close" : "View"}
                              </Button>
                            </div>
                          </div>
                          {evalDetailId === item.evaluation_id && (
                            <div className="mt-2 border-t border-border pt-2">
                              {evalDetailLoading ? (
                                <p className="text-[11px] text-muted">
                                  Loading evaluation…
                                </p>
                              ) : !evalDetail ? (
                                <p className="text-[11px] text-muted">
                                  Could not load this evaluation.
                                </p>
                              ) : (
                                <div className="space-y-2">
                                  <p className="text-[11px] text-muted">
                                    {evalDetail.evaluator_snapshot.rules.length} rule
                                    {evalDetail.evaluator_snapshot.rules.length === 1
                                      ? ""
                                      : "s"}{" "}
                                    ·{" "}
                                    {evalDetail.scoring.mode === "weighted"
                                      ? "weighted criteria"
                                      : "equal criteria"}{" "}
                                    · run {evalDetail.prompt_run_id.slice(0, 8)}
                                    {evalDetail.version_number
                                      ? ` · v${evalDetail.version_number}`
                                      : ""}
                                    {evalDetail.provider
                                      ? ` · ${evalDetail.provider} / ${evalDetail.model ?? "—"}`
                                      : ""}
                                  </p>
                                  <ul className="space-y-1">
                                    {evalDetail.verdicts.map((verdict, index) => (
                                      <li
                                        key={verdict.rule_id ?? index}
                                        className="flex items-start gap-2 text-[11px]"
                                      >
                                        <span
                                          className={
                                            verdict.passed ? "text-success" : "text-danger"
                                          }
                                        >
                                          <Icon
                                            name={verdict.passed ? "check" : "x"}
                                            size={12}
                                          />
                                        </span>
                                        <span className="min-w-0 flex-1 text-muted">
                                          {verdict.label ?? ruleLabel(verdict.type)} —{" "}
                                          {evidenceSummary(verdict.evidence)}
                                        </span>
                                        <Badge
                                          tone={verdict.passed ? "success" : "danger"}
                                        >
                                          {verdict.passed ? "PASS" : "FAIL"}
                                        </Badge>
                                      </li>
                                    ))}
                                  </ul>
                                  {evalDetail.scoring.mode === "weighted" && (
                                    <p className="text-[11px] text-faint">
                                      Weights as saved:{" "}
                                      {evalDetail.scoring.weights
                                        .map(
                                          (weight) =>
                                            `${weight.rule_id.slice(0, 6)}=${weight.weight}`,
                                        )
                                        .join(", ")}
                                    </p>
                                  )}
                                  <p className="text-[11px] text-faint">
                                    Score{" "}
                                    {evalDetail.score == null
                                      ? "—"
                                      : `${evalDetail.score}%`}{" "}
                                    · saved{" "}
                                    {new Date(evalDetail.created_at).toLocaleString()} ·
                                    read-only
                                  </p>
                                </div>
                              )}
                            </div>
                          )}
                        </li>
                        );
                      })}
                    </ul>
                  )}
                  {/* Phase 3R: neutral status for a history-driven selection — one side
                      asks for a second, two sides offer the existing Compare action
                      (which runs in the existing comparison area below). A selection
                      made only from live captures keeps Phase 3F's original UI. */}
                  {(compareSources.left === "history" ||
                    compareSources.right === "history") &&
                    (compareLeft || compareRight) && (
                      <div className="mt-2 flex flex-wrap items-center justify-between gap-2 rounded-lg border border-dashed border-border p-2">
                        <p className="text-[11px] text-muted">
                          {compareLeft && compareRight
                            ? "Two evaluations selected."
                            : "Select another evaluation to compare."}
                        </p>
                        {compareLeft && compareRight && (
                          <Button
                            variant="primary"
                            size="sm"
                            icon="layers"
                            disabled={comparing}
                            onClick={() => {
                              setCompareExpanded(true);
                              void comparePromptResults();
                            }}
                          >
                            {comparing ? "Comparing…" : "Compare"}
                          </Button>
                        )}
                      </div>
                    )}
                </div>
              </>
            )}
          </div>

          {/* Phase 3F: prompt comparison (capture two results, describe differences) */}
          <div className="mt-5 border-t border-border pt-5">
            <div className="flex items-center gap-2">
              <Icon name="layers" size={14} className="text-purple" />
              <h3 className="text-[11px] font-bold tracking-wide text-ink-soft uppercase">
                Compare two results
              </h3>
            </div>
            <p className="mt-1 text-[11px] text-faint">
              Captures two evaluated results — a live run or a stored record from
              Evaluation History — and reports, factually, how they differ:
              criteria, output, prompt, metadata, and the stored scores. Never a
              verdict on which is better; no ranking, no LLM judge.
            </p>
            {!compareExpanded ? (
              <Button
                variant="secondary"
                size="lg"
                icon="layers"
                onClick={() => setCompareExpanded(true)}
                className="mt-3 w-full"
              >
                Compare two results
              </Button>
            ) : (
              <>
                <div className="mt-3 grid grid-cols-1 gap-2 sm:grid-cols-2">
                  <CaptureSlot
                    label="Left result"
                    result={compareLeft}
                    onClear={() => setCompareSide("left", null)}
                  />
                  <CaptureSlot
                    label="Right result"
                    result={compareRight}
                    onClear={() => setCompareSide("right", null)}
                  />
                </div>
                <Button
                  variant="primary"
                  size="lg"
                  icon="layers"
                  onClick={() => void comparePromptResults()}
                  disabled={!compareLeft || !compareRight || comparing}
                  className="mt-3 w-full"
                >
                  {comparing ? "Comparing…" : "Compare results"}
                </Button>
                {comparison && <ComparisonView comparison={comparison} />}
              </>
            )}
          </div>

          {/* Phase 3G: evaluation suite (one evaluator, many targets, counts only) */}
          <div className="mt-5 border-t border-border pt-5">
            <div className="flex items-center gap-2">
              <Icon name="refresh" size={14} className="text-purple" />
              <h3 className="text-[11px] font-bold tracking-wide text-ink-soft uppercase">
                Run an evaluation suite
              </h3>
            </div>
            <p className="mt-1 text-[11px] text-faint">
              Applies the rules above to several targets — the persisted test
              run and any fresh executions you add — and reports integer counts
              only. No scores, no percentages, no ranking, no LLM judge.
            </p>
            {!suiteExpanded ? (
              <Button
                variant="secondary"
                size="lg"
                icon="refresh"
                onClick={() => setSuiteExpanded(true)}
                className="mt-3 w-full"
              >
                Run an evaluation suite
              </Button>
            ) : (
              <>
                {testResult?.run_id && (
                  <div className="mt-3 flex items-center gap-2 rounded-lg border border-border bg-paper p-2 dark:bg-raised">
                    <Badge tone="neutral">Run</Badge>
                    <p className="min-w-0 truncate text-[11px] text-muted">
                      The persisted test run is included as a target
                      automatically.
                    </p>
                  </div>
                )}
                <div className="mt-3 space-y-2">
                  {suiteTargets.map((row, index) => (
                    <div key={index} className="flex items-center gap-2">
                      <input
                        value={row}
                        onChange={(event) =>
                          setSuiteTargets((rows) =>
                            rows.map((value, i) =>
                              i === index ? event.target.value : value,
                            ),
                          )
                        }
                        placeholder={`Target ${index + 1} prompt (fresh execution)`}
                        className="min-w-0 flex-1 rounded-lg border border-border bg-paper px-2.5 py-2 text-[12px] text-ink outline-none focus:border-purple dark:bg-raised"
                      />
                      <Button
                        variant="ghost"
                        icon="x"
                        onClick={() =>
                          setSuiteTargets((rows) =>
                            rows.filter((_, i) => i !== index),
                          )
                        }
                        title="Remove target"
                      />
                    </div>
                  ))}
                </div>
                <Button
                  variant="secondary"
                  size="sm"
                  icon="plus"
                  onClick={() => setSuiteTargets((rows) => [...rows, ""])}
                  className="mt-2"
                  disabled={suiteTargets.length >= 20}
                >
                  Add target
                </Button>
                <Button
                  variant="primary"
                  size="lg"
                  icon="refresh"
                  onClick={() => void runEvaluationSuite()}
                  disabled={suiteRunning}
                  className="mt-3 w-full"
                >
                  {suiteRunning ? "Running suite…" : "Run evaluation suite"}
                </Button>
                {suiteResult && <SuiteResultView result={suiteResult} />}
              </>
            )}
          </div>

          {/* Phase 3H: prompt experiment (one evaluator, every saved version) */}
          <div className="mt-5 border-t border-border pt-5">
            <div className="flex items-center gap-2">
              <Icon name="refresh" size={14} className="text-purple" />
              <h3 className="text-[11px] font-bold tracking-wide text-ink-soft uppercase">
                Run a prompt experiment
              </h3>
            </div>
            <p className="mt-1 text-[11px] text-faint">
              Applies the rules above to every saved version of one prompt and
              reports integer counts only. No scores, no percentages, no ranking, no
              LLM judge.
            </p>
            {!experimentExpanded ? (
              <Button
                variant="secondary"
                size="lg"
                icon="refresh"
                onClick={() => setExperimentExpanded(true)}
                className="mt-3 w-full"
              >
                Run a prompt experiment
              </Button>
            ) : (
              <>
                <label className="mt-3 block text-[11px] font-semibold text-muted">
                  Saved prompt
                </label>
                {experimentPrompts.length === 0 ? (
                  <p className="mt-1 rounded-lg border border-border bg-paper p-2 text-[11px] text-muted dark:bg-raised">
                    No saved prompts yet. Save a prompt to your workspace first — an
                    experiment measures the versions you have saved.
                  </p>
                ) : (
                  <Select
                    className="mt-1 w-full"
                    value={experimentPromptId}
                    onChange={(event) => setExperimentPromptId(event.target.value)}
                  >
                    {experimentPrompts.map((item) => (
                      <option key={item.id} value={item.id}>
                        {item.title}
                      </option>
                    ))}
                  </Select>
                )}
                <Button
                  variant="primary"
                  size="lg"
                  icon="refresh"
                  className="mt-3 w-full"
                  disabled={experimentRunning || experimentPrompts.length === 0}
                  onClick={() => void runPromptExperiment()}
                >
                  {experimentRunning ? "Running experiment…" : "Run experiment"}
                </Button>
                {experimentResult && (
                  <ExperimentResultView result={experimentResult} />
                )}
              </>
            )}
          </div>
        </div>
      </Card>

      {/* Output column */}
      <Card className="flex min-h-[520px] flex-col p-6">
        <div className="flex items-start justify-between gap-3">
          <div className="flex items-center gap-2.5">
            <span className="grid h-7 w-7 place-items-center rounded-lg bg-purple-soft font-mono text-[11px] font-bold text-purple">
              02
            </span>
            <h2 className="text-sm font-bold text-ink">Your enhanced prompt</h2>
          </div>
          <Badge tone={ready ? "success" : "neutral"}>
            {saved
              ? savedVersion
                ? `Saved · v${savedVersion}`
                : "Saved"
              : ready
                ? "Ready"
                : "Empty"}
          </Badge>
        </div>
        <p className="mt-2 text-[12px] text-faint">
          {ready
            ? "Structured, sharpened, and ready to paste into any AI tool."
            : "The generated prompt will appear here."}
        </p>
        {saved && savedVersion && (
          <p className="mt-1 text-[12px] text-faint">
            Version {savedVersion} of this prompt. Edit and save again to keep the earlier
            versions.
          </p>
        )}

        <div
          className={`mt-4 mb-4 min-h-[340px] flex-1 overflow-auto rounded-xl border ${
            ready
              ? "border-border-strong bg-[#fcfbfe] dark:bg-[#201b2b]"
              : "border-dashed border-border bg-paper dark:bg-raised"
          }`}
        >
          {ready ? (
            <pre
              className="p-5 font-mono text-[12.5px] leading-[1.75] whitespace-pre-wrap text-ink-soft dark:text-[#ded8ea]"
              dangerouslySetInnerHTML={{ __html: html }}
            />
          ) : (
            <EmptyState
              icon="studio"
              title="A little magic awaits"
              body="Describe what you need above, tune the settings, then generate a polished prompt."
            />
          )}
        </div>

        <div className="grid grid-cols-[1fr_auto] gap-2">
          <div className="flex gap-2">
            <Button
              variant="primary"
              icon="copy"
              onClick={copyPrompt}
              disabled={!ready}
            >
              Copy
            </Button>
            <Button
              variant="secondary"
              icon="refresh"
              disabled={!ready}
              onClick={() => {
                const next = (depth === 3 ? 1 : depth + 1) as DepthLevel;
                setDepth(next);
                generate(next);
                showToast(
                  `Reworked with ${depthGuidance[next].label.toLowerCase()} depth.`,
                );
              }}
            >
              Rework
            </Button>
          </div>
          <div className="flex gap-2">
            <Button
              variant="secondary"
              icon="save"
              disabled={!ready || saving}
              onClick={() => void saveToWorkspace()}
              title={
                saved
                  ? "Save the text above as a new version of this prompt"
                  : "Save to workspace"
              }
            >
              {saving ? "Saving…" : saved ? "Save version" : "Save"}
            </Button>
            <Button
              variant="ghost"
              icon="x"
              disabled={!idea.trim() && !ready}
              onClick={clearAll}
              title="Clear input and output"
            >
              Clear
            </Button>
          </div>
        </div>
        <p className="mt-3 text-[11px] text-faint">
          {ready ? (
            <>
              Role:{" "}
              <span className="font-semibold text-muted">
                {roleNames[audience]}
              </span>{" "}
              · {prompt.length.toLocaleString()} characters
            </>
          ) : (
            <span className="inline-flex items-center gap-1.5">
              <Icon name="info" size={13} /> Use ⌘ + Enter to generate from
              anywhere in the editor.
            </span>
          )}
        </p>

        {/* Phase 3J: version history of the saved prompt. Every number shown comes
            from the server; "Current" is the highest reported version_number. */}
        {savedPromptId && (
          <div className="mt-4 border-t border-border pt-4">
            <p className="text-[10px] font-semibold tracking-wide text-faint uppercase">
              Version history
              {historyVersions ? ` · ${historyVersions.length}` : ""}
            </p>
            {historyLoading && !historyVersions ? (
              <p className="mt-2 text-[11px] text-muted">Loading versions…</p>
            ) : !historyVersions || historyVersions.length === 0 ? (
              <p className="mt-2 text-[11px] text-muted">
                {historyVersions ? "No saved versions yet." : "Could not load versions."}
              </p>
            ) : (
              <ul className="mt-2 space-y-1.5">
                {historyVersions.map((version) => {
                  const isCurrent =
                    version.version_number ===
                    Math.max(...historyVersions.map((item) => item.version_number));
                  const expanded = previewVersionId === version.id;
                  return (
                    <li
                      key={version.id}
                      className="rounded-lg border border-border bg-paper p-2 dark:bg-raised"
                    >
                      <div className="flex items-center justify-between gap-2">
                        <p className="min-w-0 truncate text-[12px] text-ink-soft">
                          <span className="font-mono font-bold">
                            v{version.version_number}
                          </span>{" "}
                          <span className="text-[11px] text-faint">
                            {new Date(version.created_at).toLocaleString()}
                          </span>
                        </p>
                        <span className="flex shrink-0 items-center gap-1.5">
                          {isCurrent ? (
                            <Badge tone="success">Current</Badge>
                          ) : null}
                          <Button
                            variant="ghost"
                            size="sm"
                            icon="eye"
                            onClick={() =>
                              setPreviewVersionId(expanded ? null : version.id)
                            }
                            title={expanded ? "Hide preview" : "View this version"}
                          >
                            {expanded ? "Hide" : "View"}
                          </Button>
                          {!isCurrent ? (
                            <Button
                              variant="ghost"
                              size="sm"
                              icon="save"
                              onClick={() => setRestoreTarget(version)}
                              title={`Restore v${version.version_number} as a new version`}
                            >
                              Restore
                            </Button>
                          ) : null}
                        </span>
                      </div>
                      {expanded ? (
                        <pre className="mt-2 max-h-44 overflow-auto rounded-lg border border-border bg-[#fcfbfe] p-3 font-mono text-[11.5px] leading-[1.7] whitespace-pre-wrap text-ink-soft dark:bg-[#201b2b]">
                          {version.body}
                        </pre>
                      ) : null}
                    </li>
                  );
                })}
              </ul>
            )}
          </div>
        )}
      </Card>

      {/* Phase 3J: restoring creates a NEW version, so it asks first. */}
      <ConfirmDialog
        open={restoreTarget !== null}
        onClose={() => {
          if (!restoring) setRestoreTarget(null);
        }}
        onConfirm={() => void confirmRestore()}
        title={
          restoreTarget
            ? `Restore version ${restoreTarget.version_number}?`
            : "Restore version?"
        }
        body="This will create a new version using the saved content. Your existing versions will remain unchanged."
        confirmLabel="Restore"
        tone="primary"
        icon="save"
        busy={restoring}
        busyLabel="Restoring…"
      />
    </section>
  );
}

/* ------------------------------------------------------------------ */
/* Phase 3F: comparison view components (factual, winner-free)         */
/* ------------------------------------------------------------------ */

function comparisonStateLabel(state: CriterionDiff["state"]): string {
  switch (state) {
    case "same_pass":
      return "Same: both passed";
    case "same_fail":
      return "Same: both failed";
    case "left_only_pass":
      return "Left passed only";
    case "right_only_pass":
      return "Right passed only";
  }
}

function comparisonStateTone(
  state: CriterionDiff["state"],
): "success" | "danger" | "warning" {
  switch (state) {
    case "same_pass":
      return "success";
    case "same_fail":
      return "danger";
    case "left_only_pass":
    case "right_only_pass":
      return "warning";
  }
}

function CaptureSlot({
  label,
  result,
  onClear,
}: {
  label: string;
  result: EvaluationResult | null;
  onClear: () => void;
}) {
  return (
    <div
      className={`rounded-lg border bg-paper p-2.5 dark:bg-raised ${
        result ? "border-border" : "border-dashed border-border"
      }`}
    >
      <div className="flex items-center justify-between gap-2">
        <span className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
          {label}
        </span>
        {result && (
          <button
            type="button"
            onClick={onClear}
            title={`Clear ${label.toLowerCase()}`}
            className="text-faint transition hover:text-ink"
          >
            <Icon name="x" size={12} />
          </button>
        )}
      </div>
      {result ? (
        <div className="mt-1.5 space-y-1">
          <Badge tone={result.passed ? "success" : "danger"}>
            {result.passed ? "PASS" : "FAIL"}
          </Badge>
          <p className="truncate text-[11px] text-muted">
            {result.provider} / {result.model}
            {result.run_id ? " · persisted run" : " · fresh execution"}
          </p>
          <p className="line-clamp-2 text-[11px] text-ink-soft whitespace-pre-wrap">
            {result.output}
          </p>
        </div>
      ) : (
        <p className="mt-1.5 text-[11px] text-faint">
          No result captured yet — run an evaluation, then use “Capture as
          {label === "Left result" ? " left" : " right"}”.
        </p>
      )}
    </div>
  );
}

function SuiteResultView({ result }: { result: EvaluationSuiteResult }) {
  const failed = result.total - result.passed;
  const allPassed = result.total > 0 && failed === 0;
  return (
    <div className="mt-4 space-y-3">
      {/* Counts-only summary: integers, never scores, ratios, or rankings. */}
      <div className="rounded-lg border border-border bg-paper p-3 dark:bg-raised">
        <div className="flex items-center justify-between gap-2">
          <Badge tone={allPassed ? "success" : "warning"}>
            {result.passed} of {result.total} targets passed
          </Badge>
          <span className="text-[10px] text-faint">
            {result.total} evaluated · {failed} failed
          </span>
        </div>
        <p className="mt-1.5 text-[11px] text-muted">
          One evaluator applied to {result.total} target
          {result.total === 1 ? "" : "s"} — with the same rules throughout.
        </p>
      </div>

      {/* Per-target results, preserving the request order. */}
      <ul className="space-y-1.5">
        {result.evaluations.map((evaluation, index) => (
          <li
            key={index}
            className="rounded-lg border border-border bg-paper p-2 dark:bg-raised"
          >
            <div className="flex items-center justify-between gap-2">
              <p className="min-w-0 truncate text-[12px] text-ink-soft">
                <span className="text-faint">{index + 1}.</span>{" "}
                {evaluation.run_id ? "persisted run" : "fresh execution"}
              </p>
              <Badge tone={evaluation.passed ? "success" : "danger"}>
                {evaluation.passed ? "PASS" : "FAIL"}
              </Badge>
            </div>
            <p className="mt-1 text-[11px] text-muted">
              {evaluation.provider} / {evaluation.model}
              {evaluation.latency_ms != null
                ? ` · ${evaluation.latency_ms} ms`
                : ""}
            </p>
            <p className="mt-1 line-clamp-2 text-[11px] text-ink-soft whitespace-pre-wrap">
              {evaluation.output}
            </p>
            <p className="mt-1 text-[10px] text-faint">
              {evaluation.verdicts.length} criterion
              {evaluation.verdicts.length === 1 ? "" : "ia"} ·{" "}
              {evaluation.verdicts.filter((verdict) => verdict.passed).length}{" "}
              passed
            </p>
          </li>
        ))}
      </ul>
    </div>
  );
}

/* ------------------------------------------------------------------ */
/* Phase 3H: experiment view (counts + chronological version timeline)  */
/* ------------------------------------------------------------------ */

function ordinalLabel(position: number): string {
  // Factual ordinal position, not a version_number: the response carries the
  // per-version results only, so the 1-based index is what can honestly be said.
  const suffixes = ["th", "st", "nd", "rd"];
  const remainder = position % 100;
  if (remainder >= 11 && remainder <= 13) return `${position}th`;
  return `${position}${suffixes[position % 10] ?? "th"}`;
}

function ExperimentResultView({ result }: { result: ExperimentResult }) {
  const failed = result.total_versions - result.passed;
  const allPassed = result.total_versions > 0 && failed === 0;
  return (
    <div className="mt-4 space-y-3">
      {/* Counts-only summary: integers, never scores, ratios, grades, or rankings. */}
      <div className="rounded-lg border border-border bg-paper p-3 dark:bg-raised">
        <div className="flex items-center justify-between gap-2">
          <Badge tone={allPassed ? "success" : "warning"}>
            {result.passed} of {result.total_versions} versions passed
          </Badge>
          <span className="text-[10px] text-faint">
            {result.total_versions} evaluated · {failed} failed
          </span>
        </div>
        <p className="mt-1.5 text-[11px] text-muted">
          One evaluator applied to every saved version of this prompt — the same
          rules throughout.
        </p>
      </div>

      {result.total_versions === 0 ? (
        <p className="rounded-lg border border-border bg-paper p-2 text-[11px] text-muted dark:bg-raised">
          This prompt has no saved versions yet, so there was nothing to measure.
        </p>
      ) : (
        <>
          <p className="text-[10px] font-semibold tracking-wide text-faint uppercase">
            Saved versions, oldest first
          </p>
          {/* Per-version results in ascending version order (server-decided). */}
          <ul className="space-y-1.5">
            {result.evaluations.map((evaluation, index) => (
              <li
                key={index}
                className="rounded-lg border border-border bg-paper p-2 dark:bg-raised"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="min-w-0 truncate text-[12px] text-ink-soft">
                    {ordinalLabel(index + 1)} saved version
                  </p>
                  <Badge tone={evaluation.passed ? "success" : "danger"}>
                    {evaluation.passed ? "PASS" : "FAIL"}
                  </Badge>
                </div>
                <p className="mt-1 text-[11px] text-muted">
                  {evaluation.provider} / {evaluation.model}
                  {evaluation.latency_ms != null
                    ? ` · ${evaluation.latency_ms} ms`
                    : ""}
                </p>
                <p className="mt-1 line-clamp-2 text-[11px] text-ink-soft whitespace-pre-wrap">
                  {evaluation.output}
                </p>
                <p className="mt-1 text-[10px] text-faint">
                  {evaluation.verdicts.length} criterion
                  {evaluation.verdicts.length === 1 ? "" : "ia"} ·{" "}
                  {evaluation.verdicts.filter((verdict) => verdict.passed).length}{" "}
                  passed
                </p>
              </li>
            ))}
          </ul>
        </>
      )}
    </div>
  );
}

function DiffTable({ diff }: { diff: DiffLine[] }) {
  if (diff.length === 0) {
    return (
      <p className="text-[11px] text-faint">No line-level differences.</p>
    );
  }
  return (
    <div className="max-h-44 overflow-auto rounded-lg border border-border">
      <table className="w-full font-mono text-[11.5px]">
        <tbody>
          {diff.map((entry, index) => {
            const text = entry.left_line ?? entry.right_line ?? "";
            const tone =
              entry.kind === "added"
                ? "text-success"
                : entry.kind === "removed"
                  ? "text-danger"
                  : "text-muted";
            const prefix =
              entry.kind === "added"
                ? "+ "
                : entry.kind === "removed"
                  ? "− "
                  : "  ";
            return (
              <tr
                key={index}
                className={`${tone} ${entry.kind === "context" ? "opacity-70" : ""}`}
              >
                <td className="w-6 px-2 py-0.5 text-right select-none">
                  {prefix}
                </td>
                <td className="px-1 py-0.5 break-words whitespace-pre-wrap">
                  {text}
                </td>
              </tr>
            );
          })}
        </tbody>
      </table>
    </div>
  );
}

function ValueRow({
  label,
  left,
  right,
  changed,
}: {
  label: string;
  left: string;
  right: string;
  changed: boolean;
}) {
  return (
    <div className="flex items-center justify-between gap-3 py-1">
      <span className="text-[11px] text-faint">{label}</span>
      <span
        className={`text-[11px] ${changed ? "font-semibold text-ink" : "text-muted"}`}
      >
        {left} <span className="text-faint">·</span> {right}
      </span>
    </div>
  );
}

function UsageValueRow({
  label,
  row,
}: {
  label: string;
  row: UsageFieldDiff;
}) {
  return (
    <ValueRow
      label={label}
      left={row.left != null ? String(row.left) : "n/a"}
      right={row.right != null ? String(row.right) : "n/a"}
      changed={row.changed}
    />
  );
}

function asText(value: string | number | null): string {
  return value != null ? String(value) : "n/a";
}

function ComparisonView({ comparison }: { comparison: ComparisonResult }) {
  const compat = comparison.evaluator_compatibility;
  const output = comparison.output_summary;
  return (
    <div className="mt-4 space-y-4">
      {/* Compatibility banner */}
      <div className="rounded-lg border border-border bg-paper p-3 dark:bg-raised">
        <div className="flex items-start gap-2">
          {compat.comparable ? (
            <Badge tone="success">Comparable</Badge>
          ) : (
            <Badge tone="warning">Not comparable</Badge>
          )}
          <p className="text-[11px] text-muted">
            {compat.comparable
              ? "Same evaluator on both sides — criteria are compared position by position."
              : "The two evaluators differ structurally, so criteria are not compared. Output, prompt, and metadata differences are still reported below."}
          </p>
        </div>
        {!compat.comparable && compat.mismatches.length > 0 && (
          <ul className="mt-2 space-y-1 border-t border-border pt-2">
            {compat.mismatches.map((mismatch, index) => (
              <li key={index} className="text-[11px] text-muted">
                {mismatch.rule_index === -1
                  ? `${mismatch.field}`
                  : `Rule ${mismatch.rule_index + 1} · ${mismatch.field}`}
                : <span className="font-mono">{String(mismatch.left_value)}</span>
                {" → "}
                <span className="font-mono text-ink-soft">
                  {String(mismatch.right_value)}
                </span>
              </li>
            ))}
          </ul>
        )}
      </div>

      {/* Criteria */}
      {comparison.criterion_diffs.length > 0 && (
        <div>
          <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
            Criteria
          </p>
          <ul className="mt-1.5 space-y-1.5">
            {comparison.criterion_diffs.map((diff) => (
              <li
                key={diff.rule_index}
                className="rounded-lg border border-border bg-paper p-2 text-[12px] dark:bg-raised"
              >
                <div className="flex items-center justify-between gap-2">
                  <p className="min-w-0 truncate text-ink-soft">
                    <span className="text-faint">{diff.rule_index + 1}.</span>{" "}
                    {diff.label.left ?? diff.label.right ?? ruleLabel(diff.type)}
                  </p>
                  <Badge tone={comparisonStateTone(diff.state)}>
                    {comparisonStateLabel(diff.state)}
                  </Badge>
                </div>
                {diff.evidence_delta && (
                  <p className="mt-1 text-[11px] text-muted">
                    Evidence changed:{" "}
                    {Object.entries(diff.evidence_delta).map(
                      ([key, { left, right }]) => (
                        <span key={key}>
                          {key} <span className="font-mono">{left}</span> →{" "}
                          <span className="font-mono">{right}</span>
                          {"  "}
                        </span>
                      ),
                    )}
                  </p>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}

      {/* Output diff */}
      <div>
        <div className="flex items-center justify-between gap-2">
          <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
            Output
          </p>
          {output.identical ? (
            <Badge tone="success">Identical</Badge>
          ) : (
            <Badge tone="warning">Different</Badge>
          )}
        </div>
        <p className="mt-1 text-[11px] text-muted">
          Left {output.length_a.toLocaleString()} chars · right{" "}
          {output.length_b.toLocaleString()} chars · {output.added_lines} added
          / {output.removed_lines} removed / {output.unchanged_lines} unchanged
          lines
          {output.truncated ? " · truncated for display" : ""}
        </p>
        <div className="mt-1.5">
          <DiffTable diff={output.diff} />
        </div>
      </div>

      {/* Prompt diff */}
      {comparison.prompt_diff && (
        <div>
          <div className="flex items-center justify-between gap-2">
            <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
              Executed prompt
            </p>
            {comparison.prompt_diff.identical ? (
              <Badge tone="success">Identical</Badge>
            ) : (
              <Badge tone="warning">Different</Badge>
            )}
          </div>
          <p className="mt-1 text-[11px] text-muted">
            Left {comparison.prompt_diff.left_length.toLocaleString()} chars ·
            right {comparison.prompt_diff.right_length.toLocaleString()} chars
          </p>
          <div className="mt-1.5">
            <DiffTable diff={comparison.prompt_diff.diff} />
          </div>
        </div>
      )}

      {/* Execution metadata */}
      <div>
        <p className="text-[10px] font-bold tracking-wide text-ink-soft uppercase">
          Execution metadata
        </p>
        <div className="mt-1.5 divide-y divide-border rounded-lg border border-border bg-paper px-3 dark:bg-raised">
          <ValueRow
            label="Provider"
            left={asText(comparison.metadata_diff.provider.left)}
            right={asText(comparison.metadata_diff.provider.right)}
            changed={comparison.metadata_diff.provider.changed}
          />
          <ValueRow
            label="Model"
            left={asText(comparison.metadata_diff.model.left)}
            right={asText(comparison.metadata_diff.model.right)}
            changed={comparison.metadata_diff.model.changed}
          />
          <ValueRow
            label="Finish reason"
            left={asText(comparison.metadata_diff.finish_reason.left)}
            right={asText(comparison.metadata_diff.finish_reason.right)}
            changed={comparison.metadata_diff.finish_reason.changed}
          />
          <ValueRow
            label="Latency"
            left={
              comparison.metadata_diff.latency_ms.left != null
                ? `${comparison.metadata_diff.latency_ms.left} ms`
                : "n/a"
            }
            right={
              comparison.metadata_diff.latency_ms.right != null
                ? `${comparison.metadata_diff.latency_ms.right} ms`
                : "n/a"
            }
            changed={comparison.metadata_diff.latency_ms.changed}
          />
          {comparison.metadata_diff.latency_delta != null && (
            <div className="flex items-center justify-between gap-3 py-1">
              <span className="text-[11px] text-faint">
                Latency delta (right − left)
              </span>
              <span
                className={`text-[11px] font-semibold ${
                  comparison.metadata_diff.latency_delta === 0
                    ? "text-muted"
                    : "text-ink"
                }`}
              >
                {comparison.metadata_diff.latency_delta > 0 ? "+" : ""}
                {comparison.metadata_diff.latency_delta} ms
              </span>
            </div>
          )}
          {/* Phase 3R: each side's stored score plus the server-reported delta —
              factual values only, never a ranking or a verdict. */}
          <ValueRow
            label="Score"
            left={
              comparison.left_execution.score != null
                ? `${comparison.left_execution.score}%`
                : "n/a"
            }
            right={
              comparison.right_execution.score != null
                ? `${comparison.right_execution.score}%`
                : "n/a"
            }
            changed={
              comparison.metadata_diff.score_delta != null &&
              comparison.metadata_diff.score_delta !== 0
            }
          />
          {comparison.metadata_diff.score_delta != null && (
            <div className="flex items-center justify-between gap-3 py-1">
              <span className="text-[11px] text-faint">
                Score delta (right − left)
              </span>
              <span
                className={`text-[11px] font-semibold ${
                  comparison.metadata_diff.score_delta === 0
                    ? "text-muted"
                    : "text-ink"
                }`}
              >
                {comparison.metadata_diff.score_delta > 0 ? "+" : ""}
                {comparison.metadata_diff.score_delta}%
              </span>
            </div>
          )}
          {comparison.metadata_diff.usage && (
            <>
              <UsageValueRow
                label="Prompt tokens"
                row={comparison.metadata_diff.usage.prompt_tokens}
              />
              <UsageValueRow
                label="Completion tokens"
                row={comparison.metadata_diff.usage.completion_tokens}
              />
              <UsageValueRow
                label="Total tokens"
                row={comparison.metadata_diff.usage.total_tokens}
              />
            </>
          )}
        </div>
      </div>
    </div>
  );
}
