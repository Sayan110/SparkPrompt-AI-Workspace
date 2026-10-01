export type Audience = "everyone" | "developer" | "creator" | "business" | "student";
export type OutputFormat = "best" | "steps" | "detailed" | "concise" | "table" | "code";
export type DepthLevel = 1 | 2 | 3;
export type ToolId = "summarize" | "email" | "code" | "ideas";

export type SessionUser = {
  id: string;
  email: string;
  displayName: string;
};

export type SparkEntry = {
  id: string;
  idea: string;
  role: Audience;
  prompt: string;
  createdAt: number;
};
