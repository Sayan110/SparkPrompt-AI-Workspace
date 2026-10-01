import type { Audience, DepthLevel, OutputFormat } from "./types";

export const roleGuidance: Record<Audience, string> = {
  everyone:
    "a thoughtful expert and practical collaborator. Use plain language, avoid unexplained jargon, and make the result approachable for a capable general audience.",
  developer:
    "a senior product-minded engineer. Be technically precise, make sensible implementation assumptions explicit, and favor maintainable, secure, accessible solutions.",
  creator:
    "an inventive creative director. Balance originality with usability, make the concept emotionally resonant, and include concrete stylistic direction.",
  business:
    "a pragmatic strategist. Prioritize audience insight, realistic execution, measurable outcomes, and clear trade-offs.",
  student:
    "an encouraging subject-matter tutor. Build understanding from first principles, call out misconceptions, and use memorable examples.",
};

export const roleNames: Record<Audience, string> = {
  everyone: "curious human",
  developer: "developer",
  creator: "creator",
  business: "business builder",
  student: "student / researcher",
};

export const formatGuidance: Record<OutputFormat, string> = {
  best: "Choose the clearest format for this specific request. Use headings and bullets where they make the answer easier to act on.",
  steps: "Organize the answer as a numbered, step-by-step plan. Put prerequisites and decisions before action items.",
  detailed: "Give a thorough, structured response. Cover the important details without drifting into filler.",
  concise: "Be crisp and useful. Deliver the highest-value answer first, keeping each point short and concrete.",
  table: "Present the core answer in a clean markdown table where comparison or planning benefits from it. Add brief notes below only if needed.",
  code: "Provide production-ready code with a sensible project structure, setup instructions, key implementation notes, and a short verification checklist.",
};

export const depthGuidance: Record<DepthLevel, { label: string; detail: string }> = {
  1: { label: "Quick", detail: "Keep it focused: solve the request directly and include only essential context." },
  2: { label: "Balanced", detail: "Add useful context, assumptions, and practical details that improve the result." },
  3: { label: "Deep dive", detail: "Explore the task rigorously, including edge cases, alternatives, risks, and a quality check." },
};

export function createEnhancedPrompt(input: {
  idea: string;
  audience: Audience;
  output: OutputFormat;
  depth: DepthLevel;
}): string {
  const level = depthGuidance[input.depth];
  return [
    "Act as " + roleGuidance[input.audience],
    "",
    "## Objective",
    "Help me with this request:",
    '"' + input.idea + '"',
    "",
    "## What great looks like",
    "- First, infer the real goal, intended audience, and any important constraints from my request.",
    "- If a key detail is missing, make a reasonable assumption and briefly state it. Ask a follow-up question only when the missing information would materially change the answer.",
    "- Give specific, actionable recommendations instead of generic advice.",
    "- Use realistic examples, options, or templates wherever they make the result easier to use.",
    "- " + level.detail,
    "",
    "## Response style",
    formatGuidance[input.output],
    "Write for me as a " + roleNames[input.audience] + ". Be confident, warm, and direct. Do not use fluff, repeated ideas, or vague filler.",
    "",
    "## Final quality check",
    "Before finalizing, ensure the response directly solves the request, is internally consistent, and gives me a clear next action.",
  ].join("\n");
}

export function shortTitle(text: string): string {
  const words = text.replace(/\s+/g, " ").trim().split(" ");
  return words.slice(0, 7).join(" ") + (words.length > 7 ? "…" : "");
}

export function escapeHtml(text: string): string {
  return text.replace(/[&<>"']/g, (char) => {
    const map: Record<string, string> = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return map[char] ?? char;
  });
}
