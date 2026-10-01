// Phase 3R — Compare From Stored Evaluation History.
//
// A stored EvaluationRecord is already a complete, immutable evaluation: evaluator and
// scoring snapshots by value, the verdicts as recorded, and the score as stored. Phase
// 3P proved on the server that such a record can be handed to the Phase 3F comparison
// as a supplied result; this module is the client-side half of that reuse.
//
// It ONLY renames fields. No verdict, score, evidence, output, or metadata value is
// ever computed here, nothing is re-evaluated, and nothing here ranks the two sides or
// picks a preferred one — the comparison contract stays the factual.

import type { EvaluationRecordDetail, EvaluationResult } from "@/lib/api";

/**
 * Maps one stored evaluation record onto the supplied-comparison contract.
 *
 * Field-for-field mirror of the server's own
 * `EvaluationRecordRead.to_evaluation_result()`: identities are renamed to the Phase 3E
 * wire names (`prompt_run_id` → `run_id`, `prompt_version_id` → `version_id`) and every
 * other value passes through untouched. The server stays authoritative — it re-derives
 * `score` from the stored verdicts under the stored profile when it validates this
 * payload, so a client-supplied value can never change what is compared.
 */
export function historyToEvaluationResult(
  detail: EvaluationRecordDetail,
): EvaluationResult {
  return {
    run_id: detail.prompt_run_id,
    output: detail.output,
    provider: detail.provider,
    model: detail.model,
    latency_ms: detail.latency_ms,
    usage: detail.usage,
    evaluator_snapshot: detail.evaluator_snapshot,
    verdicts: detail.verdicts,
    passed: detail.passed,
    score: detail.score,
    scoring: detail.scoring,
    version_id: detail.prompt_version_id,
    evaluation_id: detail.evaluation_id,
    generated_at: detail.created_at,
  };
}

/** The two comparison slots the existing Studio compare area already maintains. */
export type CompareSlot = "left" | "right";

/**
 * Where a slot was filled from — the only thing that decides WHICH of the two existing
 * comparison modes runs:
 *
 * - `history`: a stored record. Always compared exactly as saved (supplied mode). Run-pair
 *   mode would re-score both runs with the rules currently in the editor, which is not
 *   what the stored evaluation said.
 * - `capture`: a live result from this session. Keeps Phase 3F's original behavior —
 *   run-pair mode when both sides are persisted runs, supplied mode otherwise.
 */
export type CompareSource = "history" | "capture";

export type CompareSources = Record<CompareSlot, CompareSource>;

/** Which history rows (by evaluation id) currently sit in each slot. */
export type HistorySelection = { left: string | null; right: string | null };

export type HistoryChoice =
  /** Row already occupies that slot: activating it again clears that side only. */
  | { action: "clear"; side: CompareSlot }
  /** Row joins the first free slot, or replaces the most recent one when both are full. */
  | { action: "set"; side: CompareSlot };

/**
 * Decides where a clicked history row goes, in one place so the rule "one evaluation
 * can never occupy both sides" cannot be forgotten at the call site: a row already held
 * by a side can only be cleared from that side, never added to the other, and every
 * other row lands on the left first, then the right.
 */
export function historySelectionChoice(
  selection: HistorySelection,
  evaluationId: string,
): HistoryChoice {
  if (selection.left === evaluationId) return { action: "clear", side: "left" };
  if (selection.right === evaluationId) return { action: "clear", side: "right" };
  if (selection.left === null) return { action: "set", side: "left" };
  if (selection.right === null) return { action: "set", side: "right" };
  // Both sides are taken: the first pick is kept, the second follows the latest click.
  return { action: "set", side: "right" };
}
