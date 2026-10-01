import type { ToolId } from "./types";

export type ToolDefinition = {
  id: ToolId;
  badge: string;
  title: string;
  description: string;
  action: string;
  empty: string;
};

export const tools: ToolDefinition[] = [
  {
    id: "summarize",
    badge: "TEXT TOOL",
    title: "Quick summary",
    description: "Pull the essential message from notes, articles, or meeting text.",
    action: "Summarize text",
    empty: "Your key points will appear here.",
  },
  {
    id: "email",
    badge: "WRITING TOOL",
    title: "Email helper",
    description: "Turn a few rough notes into a considerate, ready-to-send email.",
    action: "Draft email",
    empty: "Your email draft will appear here.",
  },
  {
    id: "code",
    badge: "DEVELOPER TOOL",
    title: "Code clarity",
    description: "Get a plain-language explanation of what a small code snippet is doing.",
    action: "Explain code",
    empty: "A plain-language walkthrough will appear here.",
  },
  {
    id: "ideas",
    badge: "CREATIVE TOOL",
    title: "Idea spark",
    description: "Create a handful of useful directions from one starting topic.",
    action: "Spark ideas",
    empty: "A set of fresh angles will appear here.",
  },
];

function sentenceList(text: string): string[] {
  return text.replace(/\s+/g, " ").match(/[^.!?]+[.!?]+|[^.!?]+$/g) ?? [];
}

export function makeSummary(input: string, length: "short" | "medium" | "long", focus: string): string {
  const trimmed = input.trim();
  if (!trimmed) return "Paste some text first, then try again.";
  const wanted = length === "short" ? 2 : length === "long" ? 5 : 3;
  const sentences = sentenceList(trimmed)
    .map((sentence) => sentence.trim())
    .filter(Boolean);
  const selected = sentences.slice(0, wanted);
  const points = selected.length ? selected : [trimmed.slice(0, 300)];
  let result = "KEY POINTS\n" + points.map((point, index) => `${index + 1}. ${point}`).join("\n");
  if (focus.trim()) {
    result += `\n\nFOCUS TO REVIEW\n${focus.trim()}: use these points as a starting place, then verify the source details.`;
  }
  return result;
}

export function makeEmail(recipient: string, notes: string, tone: "friendly" | "professional" | "direct"): string {
  const trimmed = notes.trim();
  if (!trimmed) return "Add a few notes about the email first, then try again.";
  const to = recipient.trim() || "there";
  const subjectWords = trimmed.replace(/\s+/g, " ").split(" ").slice(0, 7).join(" ");
  const opener =
    tone === "friendly"
      ? "I hope you are doing well."
      : tone === "direct"
        ? "I am reaching out with a quick update."
        : "I hope this message finds you well.";
  const close = tone === "friendly" ? "Thanks so much," : tone === "direct" ? "Thanks," : "Kind regards,";
  return `SUBJECT: ${subjectWords.charAt(0).toUpperCase()}${subjectWords.slice(1)}\n\nHi ${to},\n\n${opener}\n\n${trimmed}\n\nPlease let me know if you have any questions or if a quick follow-up would be helpful.\n\n${close}\n[Your name]`;
}

function detectLanguage(code: string): string {
  if (/<[a-z][\s\S]*>/i.test(code)) return "HTML";
  if (/\bdef\s+\w+|print\(|import\s+\w+/.test(code)) return "Python";
  if (/\bfunction\s+\w+|const\s+|let\s+|=>/.test(code)) return "JavaScript";
  if (/\bclass\s+\w+|public\s+static|System\.out/.test(code)) return "Java or C#";
  if (/\{\s*[\w-]+\s*:/.test(code)) return "CSS";
  return "code";
}

export function makeCodeExplanation(code: string, level: "beginner" | "technical", goal: string): string {
  const trimmed = code.trim();
  if (!trimmed) return "Paste a short code snippet first, then try again.";
  const language = detectLanguage(trimmed);
  const lines = trimmed
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const actions: string[] = [];
  if (/\b(fetch|axios|XMLHttpRequest)\b/i.test(trimmed)) actions.push("gets data from another service");
  if (/\b(map|filter|reduce)\b/.test(trimmed)) actions.push("transforms a list of values");
  if (/\bfor\b|\bwhile\b/.test(trimmed)) actions.push("repeats work over values");
  if (/\breturn\b/.test(trimmed)) actions.push("returns a result to the caller");
  if (!actions.length) actions.push("defines the logic needed for its task");
  let result = `OVERVIEW\nThis looks like ${language}. It mainly ${actions.join(" and ")}.\n\nFLOW\n1. It starts with: ${lines.slice(0, 2).join(" ")}\n2. Review variable names, inputs, and outputs to understand the exact data path.`;
  if (level === "beginner") {
    result += "\n\nBEGINNER TIP\nRead it one line at a time and ask: what data comes in, what changes, and what comes out?";
  }
  if (goal.trim()) {
    result += `\n\nCHECKING FOR\n${goal.trim()}: focus your review on the input values, conditional branches, and any error handling.`;
  }
  return result;
}

export function makeIdeas(topic: string, audience: string, style: "practical" | "content" | "campaign"): string {
  const trimmed = topic.trim();
  if (!trimmed) return "Describe a topic or challenge first, then try again.";
  const who = audience.trim() || "your audience";
  const starters =
    style === "content"
      ? [
          "A short behind-the-scenes story",
          "A myth-versus-fact post",
          "A customer question series",
          "A practical checklist",
          "A before-and-after example",
          "A local collaboration spotlight",
        ]
      : style === "campaign"
        ? [
            "A small launch challenge",
            "A partner-led giveaway",
            "A themed weekly series",
            "A referral moment",
            "A community pop-up",
            "A limited-time bundle",
          ]
        : [
            "A one-week experiment",
            "A simple customer interview",
            "A low-cost prototype",
            "A collaboration with a local partner",
            "A clear feedback loop",
            "A measurable pilot",
          ];
  return (
    "IDEAS FOR " +
    who.toUpperCase() +
    "\n" +
    starters.map((starter, index) => `${index + 1}. ${starter} for ${trimmed}.`).join("\n") +
    "\n\nNEXT STEP\nPick one idea that is easy to test this week and decide what result would count as success."
  );
}
