export const recipes = [
  {
    className: "bg-lavender",
    icon: "⌘",
    title: "Build something",
    body: "Apps, code, websites & systems",
    idea: "I need to build a responsive habit-tracking app with streaks, reminders, and a calming interface",
    audience: "developer" as const,
    output: "code" as const,
  },
  {
    className: "bg-peach",
    icon: "✺",
    title: "Make it memorable",
    body: "Content, design & creative ideas",
    idea: "Create a content strategy for launching a handmade candle brand on social media",
    audience: "creator" as const,
    output: "detailed" as const,
  },
  {
    className: "bg-mint",
    icon: "↗",
    title: "Grow an idea",
    body: "Strategy, marketing & decisions",
    idea: "Design a simple plan to validate a new meal-prep delivery business before spending much money",
    audience: "business" as const,
    output: "steps" as const,
  },
  {
    className: "bg-yellow",
    icon: "▣",
    title: "Learn anything",
    body: "Research, explainers & study",
    idea: "Help me learn the basics of machine learning and build a study plan for the next 30 days",
    audience: "student" as const,
    output: "steps" as const,
  },
];

export const examples = [
  { label: "Plan a Japan trip", idea: "Plan a 7-day trip to Japan for a first-time visitor" },
  { label: "Marketing plan", idea: "Create a marketing plan for a sustainable clothing brand" },
  { label: "Explain a hard idea", idea: "Explain quantum computing to a 12-year-old" },
];
