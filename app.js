const roughPrompt = document.getElementById("roughPrompt");
const audience = document.getElementById("audience");
const output = document.getElementById("output");
const depth = document.getElementById("depth");
const depthLabel = document.getElementById("depthLabel");
const promptOutput = document.getElementById("promptOutput");
const outputSubline = document.getElementById("outputSubline");
const qualityPill = document.getElementById("qualityPill");
const generateButton = document.getElementById("generateButton");
const copyButton = document.getElementById("copyButton");
const regenerateButton = document.getElementById("regenerateButton");
const saveButton = document.getElementById("saveButton");
const historyList = document.getElementById("historyList");
const toast = document.getElementById("toast");
const emptyState = '<div class="empty-state"><div class="orb"><span>✦</span></div><h3>A little magic awaits</h3><p>Tell us what you are trying to do.<br />We will turn it into a prompt worth using.</p></div>';

let currentPrompt = "";
let toastTimer;

const roleGuidance = {
  everyone: "a thoughtful expert and practical collaborator. Use plain language, avoid unexplained jargon, and make the result approachable for a capable general audience.",
  developer: "a senior product-minded engineer. Be technically precise, make sensible implementation assumptions explicit, and favor maintainable, secure, accessible solutions.",
  creator: "an inventive creative director. Balance originality with usability, make the concept emotionally resonant, and include concrete stylistic direction.",
  business: "a pragmatic strategist. Prioritize audience insight, realistic execution, measurable outcomes, and clear trade-offs.",
  student: "an encouraging subject-matter tutor. Build understanding from first principles, call out misconceptions, and use memorable examples."
};

const roleNames = {
  everyone: "curious human",
  developer: "developer",
  creator: "creator",
  business: "business builder",
  student: "student / researcher"
};

const formatGuidance = {
  best: "Choose the clearest format for this specific request. Use headings and bullets where they make the answer easier to act on.",
  steps: "Organize the answer as a numbered, step-by-step plan. Put prerequisites and decisions before action items.",
  detailed: "Give a thorough, structured response. Cover the important details without drifting into filler.",
  concise: "Be crisp and useful. Deliver the highest-value answer first, keeping each point short and concrete.",
  table: "Present the core answer in a clean markdown table where comparison or planning benefits from it. Add brief notes below only if needed.",
  code: "Provide production-ready code with a sensible project structure, setup instructions, key implementation notes, and a short verification checklist."
};

const depthGuidance = {
  1: { label: "Quick", detail: "Keep it focused: solve the request directly and include only essential context." },
  2: { label: "Balanced", detail: "Add useful context, assumptions, and practical details that improve the result." },
  3: { label: "Deep dive", detail: "Explore the task rigorously, including edge cases, alternatives, risks, and a quality check." }
};

function escapeHtml(text) {
  return String(text).replace(/[&<>"']/g, function(char) {
    const map = { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#039;" };
    return map[char];
  });
}

function shortTitle(text) {
  const words = text.replace(/\s+/g, " ").trim().split(" ");
  return words.slice(0, 7).join(" ") + (words.length > 7 ? "…" : "");
}

function createPrompt() {
  const idea = roughPrompt.value.trim();
  if (!idea) {
    roughPrompt.focus();
    showToast("Start with your rough idea — even a few words work.");
    return;
  }

  const level = depthGuidance[depth.value];
  const selectedRole = audience.value;
  const selectedOutput = output.value;
  currentPrompt = [
    "Act as " + roleGuidance[selectedRole],
    "",
    "## Objective",
    "Help me with this request:",
    '"' + idea + '"',
    "",
    "## What great looks like",
    "- First, infer the real goal, intended audience, and any important constraints from my request.",
    "- If a key detail is missing, make a reasonable assumption and briefly state it. Ask a follow-up question only when the missing information would materially change the answer.",
    "- Give specific, actionable recommendations instead of generic advice.",
    "- Use realistic examples, options, or templates wherever they make the result easier to use.",
    "- " + level.detail,
    "",
    "## Response style",
    formatGuidance[selectedOutput],
    "Write for me as a " + roleNames[selectedRole] + ". Be confident, warm, and direct. Do not use fluff, repeated ideas, or vague filler.",
    "",
    "## Final quality check",
    "Before finalizing, ensure the response directly solves the request, is internally consistent, and gives me a clear next action."
  ].join("\n");

  renderPrompt(currentPrompt);
  addHistory(idea, selectedRole);
  outputSubline.textContent = "Structured, sharpened, and ready to paste into your favorite AI.";
  qualityPill.textContent = depth.value === "3" ? "DEEP MAGIC" : "READY TO USE";
  copyButton.disabled = false;
  regenerateButton.disabled = false;
  saveButton.disabled = false;
}

function renderPrompt(prompt) {
  let html = escapeHtml(prompt);
  html = html.replace(/^## (.+)$/gm, '<span class="prompt-section">$1</span>');
  html = html.replace(/^Act as (.+)$/m, '<span class="prompt-label">MAGICALLY OPTIMIZED PROMPT</span>Act as <span class="highlight">$1</span>');
  promptOutput.classList.remove("empty");
  promptOutput.innerHTML = '<article class="generated-prompt">' + html + '</article>';
}

function getHistory() {
  try {
    return JSON.parse(localStorage.getItem("sparkprompt-history")) || [];
  } catch (error) {
    return [];
  }
}

function addHistory(idea, role) {
  const entries = getHistory();
  const fresh = [{ idea: idea, role: role, createdAt: Date.now() }]
    .concat(entries.filter(function(entry) { return entry.idea !== idea; }))
    .slice(0, 5);
  localStorage.setItem("sparkprompt-history", JSON.stringify(fresh));
  renderHistory();
}

function renderHistory() {
  const entries = getHistory();
  if (!entries.length) {
    historyList.innerHTML = '<p class="no-history">Your recent prompts will show up here.</p>';
    return;
  }
  historyList.innerHTML = entries.map(function(entry) {
    const safeIdea = escapeHtml(entry.idea);
    const safeRole = escapeHtml(roleNames[entry.role] || "idea");
    return '<button type="button" class="history-item" data-idea="' + safeIdea + '" aria-label="Use prompt: ' + safeIdea + '"><span class="history-spark">✦</span><span class="history-text">' + escapeHtml(shortTitle(entry.idea)) + '</span><span class="history-meta">' + safeRole + '</span></button>';
  }).join("");
}

function showToast(message) {
  clearTimeout(toastTimer);
  toast.textContent = message;
  toast.classList.add("show");
  toastTimer = setTimeout(function() { toast.classList.remove("show"); }, 2600);
}

function resetComposer() {
  roughPrompt.value = "";
  document.getElementById("charCount").textContent = "0";
  currentPrompt = "";
  promptOutput.className = "prompt-output empty";
  promptOutput.innerHTML = emptyState;
  outputSubline.textContent = "The good stuff will appear here.";
  qualityPill.textContent = "READY TO SPARK";
  [copyButton, regenerateButton, saveButton].forEach(function(button) { button.disabled = true; });
  roughPrompt.focus();
}

roughPrompt.addEventListener("input", function() {
  document.getElementById("charCount").textContent = roughPrompt.value.length;
});

depth.addEventListener("input", function() {
  depthLabel.textContent = depthGuidance[depth.value].label;
});

generateButton.addEventListener("click", createPrompt);

roughPrompt.addEventListener("keydown", function(event) {
  if ((event.metaKey || event.ctrlKey) && event.key === "Enter") createPrompt();
});

copyButton.addEventListener("click", async function() {
  if (!currentPrompt) return;
  try {
    await navigator.clipboard.writeText(currentPrompt);
    copyButton.innerHTML = "<span>✓</span> Copied!";
    showToast("Your enhanced prompt is on the clipboard.");
    setTimeout(function() { copyButton.innerHTML = "<span>⧉</span> Copy prompt"; }, 1700);
  } catch (error) {
    showToast("Copying is unavailable here. Select the text and copy it.");
  }
});

regenerateButton.addEventListener("click", function() {
  if (!roughPrompt.value.trim()) return;
  depth.value = String(depth.value === "3" ? 1 : Number(depth.value) + 1);
  depthLabel.textContent = depthGuidance[depth.value].label;
  createPrompt();
  showToast("Reworked with " + depthGuidance[depth.value].label.toLowerCase() + " magic.");
});

saveButton.addEventListener("click", function() {
  if (!currentPrompt) return;
  const saved = JSON.parse(localStorage.getItem("sparkprompt-saved") || "[]");
  saved.unshift({ prompt: currentPrompt, createdAt: Date.now() });
  localStorage.setItem("sparkprompt-saved", JSON.stringify(saved.slice(0, 15)));
  saveButton.textContent = "♥";
  showToast("Saved in this browser.");
});

document.querySelectorAll("[data-example]").forEach(function(button) {
  button.addEventListener("click", function() {
    roughPrompt.value = button.dataset.example;
    document.getElementById("charCount").textContent = roughPrompt.value.length;
    roughPrompt.focus();
  });
});

document.querySelectorAll("[data-recipe]").forEach(function(button) {
  button.addEventListener("click", function() {
    roughPrompt.value = button.dataset.recipe;
    audience.value = button.dataset.audience || "everyone";
    output.value = button.dataset.output || "best";
    document.getElementById("charCount").textContent = roughPrompt.value.length;
    document.getElementById("composer").scrollIntoView({ behavior: "smooth", block: "start" });
    setTimeout(createPrompt, 350);
  });
});

historyList.addEventListener("click", function(event) {
  const item = event.target.closest(".history-item");
  if (!item) return;
  roughPrompt.value = item.dataset.idea;
  document.getElementById("charCount").textContent = roughPrompt.value.length;
  document.getElementById("composer").scrollIntoView({ behavior: "smooth", block: "start" });
  setTimeout(createPrompt, 250);
});

document.getElementById("clearHistory").addEventListener("click", function() {
  localStorage.removeItem("sparkprompt-history");
  renderHistory();
  showToast("Recent sparks cleared.");
});

document.getElementById("newPrompt").addEventListener("click", resetComposer);

const helpModal = document.getElementById("helpModal");
function closeHelp() { helpModal.hidden = true; }
document.getElementById("helpButton").addEventListener("click", function() { helpModal.hidden = false; });
document.getElementById("closeHelp").addEventListener("click", closeHelp);
document.getElementById("gotIt").addEventListener("click", closeHelp);
helpModal.addEventListener("click", function(event) { if (event.target === helpModal) closeHelp(); });

document.getElementById("themeToggle").addEventListener("click", function() {
  document.body.classList.toggle("dark");
  const isDark = document.body.classList.contains("dark");
  document.querySelector(".theme-toggle span:nth-child(2)").textContent = isDark ? "Light mode" : "Dark mode";
  document.querySelector(".toggle-icon").textContent = isDark ? "☀" : "☾";
  document.querySelector(".toggle-track i").style.transform = isDark ? "translateX(11px)" : "translateX(0)";
  localStorage.setItem("sparkprompt-theme", isDark ? "dark" : "light");
});

document.getElementById("mobileMenu").addEventListener("click", function() {
  document.body.classList.toggle("menu-open");
});

document.querySelectorAll(".nav-link").forEach(function(link) {
  link.addEventListener("click", function() { document.body.classList.remove("menu-open"); });
});

if (localStorage.getItem("sparkprompt-theme") === "dark") document.getElementById("themeToggle").click();
renderHistory();

const loginScreen = document.getElementById("loginScreen");
const appShell = document.getElementById("appShell");
const loginForm = document.getElementById("loginForm");
const loginEmail = document.getElementById("loginEmail");
const loginPassword = document.getElementById("loginPassword");
const rememberMe = document.getElementById("rememberMe");
const guestButton = document.getElementById("guestButton");
const logoutButton = document.getElementById("logoutButton");
const userName = document.getElementById("userName");
const userInitial = document.getElementById("userInitial");
const toolPanel = document.getElementById("toolPanel");
const toolCards = document.querySelectorAll(".tool-card");
let activeTool = "summarize";

function readLocal(key) {
  try { return localStorage.getItem(key); } catch (error) { return null; }
}

function writeLocal(key, value) {
  try { localStorage.setItem(key, value); } catch (error) {}
}

function removeLocal(key) {
  try { localStorage.removeItem(key); } catch (error) {}
}

function displayNameFromEmail(email) {
  const source = (email || "Guest").split("@")[0].replace(/[._-]+/g, " ").trim();
  return source.split(" ").filter(Boolean).map(function(part) {
    return part.charAt(0).toUpperCase() + part.slice(1);
  }).join(" ") || "Guest";
}

function enterWorkspace(name, shouldRemember, quiet) {
  const cleanName = name || "Guest";
  userName.textContent = cleanName;
  userInitial.textContent = cleanName.charAt(0).toUpperCase();
  loginScreen.hidden = true;
  appShell.hidden = false;
  if (shouldRemember) writeLocal("sparkprompt-user", cleanName);
  else removeLocal("sparkprompt-user");
  if (!quiet) showToast("Welcome, " + cleanName + ". Your workspace is ready.");
}

loginForm.addEventListener("submit", function(event) {
  event.preventDefault();
  if (!loginForm.reportValidity()) return;
  enterWorkspace(displayNameFromEmail(loginEmail.value), rememberMe.checked, false);
  loginPassword.value = "";
});

guestButton.addEventListener("click", function() {
  enterWorkspace("Guest", false, false);
});

logoutButton.addEventListener("click", function() {
  removeLocal("sparkprompt-user");
  appShell.hidden = true;
  loginScreen.hidden = false;
  loginForm.reset();
  loginEmail.focus();
});

const rememberedUser = readLocal("sparkprompt-user");
if (rememberedUser) enterWorkspace(rememberedUser, true, true);

const toolContent = {
  summarize: {
    badge: "TEXT TOOL",
    title: "Quick summary",
    description: "Pull the essential message from notes, articles, or meeting text.",
    form: '<div class="tool-form-grid"><div class="tool-field span-two"><label for="summaryInput">Paste text to summarize</label><textarea id="summaryInput" placeholder="Paste your notes, article, meeting transcript, or draft here..."></textarea></div><div class="tool-field"><label for="summaryLength">Summary length</label><select id="summaryLength"><option value="short">Short — 2 key points</option><option value="medium" selected>Balanced — 3 key points</option><option value="long">Detailed — 5 key points</option></select></div><div class="tool-field"><label for="summaryFocus">Focus on</label><input id="summaryFocus" placeholder="Optional: decisions, actions, risks..." /></div></div>',
    action: "Summarize text",
    empty: "Your key points will appear here."
  },
  email: {
    badge: "WRITING TOOL",
    title: "Email helper",
    description: "Turn a few rough notes into a considerate, ready-to-send email.",
    form: '<div class="tool-form-grid"><div class="tool-field"><label for="emailTo">Recipient</label><input id="emailTo" placeholder="e.g. Priya, hiring manager" /></div><div class="tool-field"><label for="emailTone">Tone</label><select id="emailTone"><option value="friendly">Friendly and warm</option><option value="professional" selected>Professional and clear</option><option value="direct">Direct and concise</option></select></div><div class="tool-field span-two"><label for="emailNotes">What should the email say?</label><textarea id="emailNotes" placeholder="Example: Ask Priya for feedback on the proposal by Friday and thank her for the earlier help."></textarea></div></div>',
    action: "Draft email",
    empty: "Your email draft will appear here."
  },
  code: {
    badge: "DEVELOPER TOOL",
    title: "Code clarity",
    description: "Get a plain-language explanation of what a small code snippet is doing.",
    form: '<div class="tool-form-grid"><div class="tool-field span-two"><label for="codeInput">Paste a code snippet</label><textarea id="codeInput" placeholder="Paste JavaScript, Python, HTML, CSS, or another small snippet..."></textarea></div><div class="tool-field"><label for="codeLevel">Explanation level</label><select id="codeLevel"><option value="beginner" selected>Beginner-friendly</option><option value="technical">Technical overview</option></select></div><div class="tool-field"><label for="codeGoal">What are you checking?</label><input id="codeGoal" placeholder="Optional: bug, logic, performance..." /></div></div>',
    action: "Explain code",
    empty: "A plain-language walkthrough will appear here."
  },
  ideas: {
    badge: "CREATIVE TOOL",
    title: "Idea spark",
    description: "Create a handful of useful directions from one starting topic.",
    form: '<div class="tool-form-grid"><div class="tool-field span-two"><label for="ideaTopic">Topic or challenge</label><textarea id="ideaTopic" placeholder="Example: Help a neighborhood bookstore bring in more weekend visitors"></textarea></div><div class="tool-field"><label for="ideaAudience">Audience</label><input id="ideaAudience" placeholder="e.g. students, new customers" /></div><div class="tool-field"><label for="ideaStyle">Idea type</label><select id="ideaStyle"><option value="practical" selected>Practical actions</option><option value="content">Content ideas</option><option value="campaign">Campaign angles</option></select></div></div>',
    action: "Spark ideas",
    empty: "A set of fresh angles will appear here."
  }
};

function renderTool(toolId) {
  activeTool = toolId;
  const tool = toolContent[toolId];
  toolPanel.innerHTML = '<div class="tool-head"><div><span class="tool-badge">' + tool.badge + '</span><h3>' + tool.title + '</h3><p>' + tool.description + '</p></div></div>' + tool.form + '<div class="tool-actions"><button class="tool-run" id="toolRun" type="button">✦ ' + tool.action + '</button><button class="tool-copy" id="toolCopy" type="button" disabled>⧉ Copy result</button></div><div class="tool-result" id="toolResult">' + tool.empty + '</div><p class="tool-note">Local helper — your input stays in this browser.</p>';
  document.getElementById("toolRun").addEventListener("click", runTool);
  document.getElementById("toolCopy").addEventListener("click", copyToolResult);
  toolCards.forEach(function(card) {
    const isActive = card.dataset.tool === toolId;
    card.classList.toggle("active", isActive);
    card.setAttribute("aria-selected", String(isActive));
  });
}

function toolResult(text) {
  const result = document.getElementById("toolResult");
  result.textContent = text;
  result.classList.add("has-result");
  document.getElementById("toolCopy").disabled = false;
}

function sentenceList(text) {
  return text.replace(/\s+/g, " ").match(/[^.!?]+[.!?]+|[^.!?]+$/g) || [];
}

function makeSummary() {
  const input = document.getElementById("summaryInput").value.trim();
  const length = document.getElementById("summaryLength").value;
  const focus = document.getElementById("summaryFocus").value.trim();
  if (!input) return "Paste some text first, then try again.";
  const wanted = length === "short" ? 2 : length === "long" ? 5 : 3;
  const sentences = sentenceList(input).map(function(sentence) { return sentence.trim(); }).filter(Boolean);
  const selected = sentences.slice(0, wanted);
  const points = selected.length ? selected : [input.slice(0, 300)];
  let result = "KEY POINTS\n" + points.map(function(point, index) { return (index + 1) + ". " + point; }).join("\n");
  if (focus) result += "\n\nFOCUS TO REVIEW\n" + focus + ": use these points as a starting place, then verify the source details.";
  return result;
}

function makeEmail() {
  const recipient = document.getElementById("emailTo").value.trim() || "there";
  const notes = document.getElementById("emailNotes").value.trim();
  const tone = document.getElementById("emailTone").value;
  if (!notes) return "Add a few notes about the email first, then try again.";
  const subjectWords = notes.replace(/\s+/g, " ").split(" ").slice(0, 7).join(" ");
  const opener = tone === "friendly" ? "I hope you are doing well." : tone === "direct" ? "I am reaching out with a quick update." : "I hope this message finds you well.";
  const close = tone === "friendly" ? "Thanks so much," : tone === "direct" ? "Thanks," : "Kind regards,";
  return "SUBJECT: " + subjectWords.charAt(0).toUpperCase() + subjectWords.slice(1) + "\n\nHi " + recipient + ",\n\n" + opener + "\n\n" + notes + "\n\nPlease let me know if you have any questions or if a quick follow-up would be helpful.\n\n" + close + "\n[Your name]";
}

function detectLanguage(code) {
  if (/<[a-z][\s\S]*>/i.test(code)) return "HTML";
  if (/\bdef\s+\w+|print\(|import\s+\w+/.test(code)) return "Python";
  if (/\bfunction\s+\w+|const\s+|let\s+|=>/.test(code)) return "JavaScript";
  if (/\bclass\s+\w+|public\s+static|System\.out/.test(code)) return "Java or C#";
  if (/\{\s*[\w-]+\s*:/.test(code)) return "CSS";
  return "code";
}

function makeCodeExplanation() {
  const code = document.getElementById("codeInput").value.trim();
  const level = document.getElementById("codeLevel").value;
  const goal = document.getElementById("codeGoal").value.trim();
  if (!code) return "Paste a short code snippet first, then try again.";
  const language = detectLanguage(code);
  const lines = code.split("\n").map(function(line) { return line.trim(); }).filter(Boolean);
  const actions = [];
  if (/\b(fetch|axios|XMLHttpRequest)\b/i.test(code)) actions.push("gets data from another service");
  if (/\b(map|filter|reduce)\b/.test(code)) actions.push("transforms a list of values");
  if (/\bfor\b|\bwhile\b/.test(code)) actions.push("repeats work over values");
  if (/\breturn\b/.test(code)) actions.push("returns a result to the caller");
  if (!actions.length) actions.push("defines the logic needed for its task");
  let result = "OVERVIEW\nThis looks like " + language + ". It mainly " + actions.join(" and ") + ".\n\nFLOW\n1. It starts with: " + lines.slice(0, 2).join(" ") + "\n2. Review variable names, inputs, and outputs to understand the exact data path.";
  if (level === "beginner") result += "\n\nBEGINNER TIP\nRead it one line at a time and ask: what data comes in, what changes, and what comes out?";
  if (goal) result += "\n\nCHECKING FOR\n" + goal + ": focus your review on the input values, conditional branches, and any error handling.";
  return result;
}

function makeIdeas() {
  const topic = document.getElementById("ideaTopic").value.trim();
  const audience = document.getElementById("ideaAudience").value.trim() || "your audience";
  const style = document.getElementById("ideaStyle").value;
  if (!topic) return "Describe a topic or challenge first, then try again.";
  const starters = style === "content" ? ["A short behind-the-scenes story", "A myth-versus-fact post", "A customer question series", "A practical checklist", "A before-and-after example", "A local collaboration spotlight"] : style === "campaign" ? ["A small launch challenge", "A partner-led giveaway", "A themed weekly series", "A referral moment", "A community pop-up", "A limited-time bundle"] : ["A one-week experiment", "A simple customer interview", "A low-cost prototype", "A collaboration with a local partner", "A clear feedback loop", "A measurable pilot"];
  return "IDEAS FOR " + audience.toUpperCase() + "\n" + starters.map(function(starter, index) { return (index + 1) + ". " + starter + " for " + topic + "."; }).join("\n") + "\n\nNEXT STEP\nPick one idea that is easy to test this week and decide what result would count as success.";
}

function runTool() {
  let result = "";
  if (activeTool === "summarize") result = makeSummary();
  if (activeTool === "email") result = makeEmail();
  if (activeTool === "code") result = makeCodeExplanation();
  if (activeTool === "ideas") result = makeIdeas();
  toolResult(result);
}

async function copyToolResult() {
  const result = document.getElementById("toolResult").textContent;
  if (!result || document.getElementById("toolCopy").disabled) return;
  try {
    await navigator.clipboard.writeText(result);
    document.getElementById("toolCopy").textContent = "✓ Copied";
    setTimeout(function() { const copy = document.getElementById("toolCopy"); if (copy) copy.textContent = "⧉ Copy result"; }, 1500);
    showToast("Tool result copied to your clipboard.");
  } catch (error) {
    showToast("Copying is unavailable here. Select the result and copy it.");
  }
}

toolCards.forEach(function(card) {
  card.addEventListener("click", function() { renderTool(card.dataset.tool); });
});

renderTool(activeTool);
