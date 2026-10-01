"use client";

import Link from "next/link";
import { type FormEvent, useEffect, useState } from "react";
import { useRouter } from "next/navigation";

import { Brand } from "@/components/brand";
import { Icon } from "@/components/icons";
import { Button, Card, Input } from "@/components/ui";
import { ApiError } from "@/lib/api";
import { friendlyApiError } from "@/lib/errors";
import { useToast } from "@/lib/toast";
import { useWorkspace } from "@/lib/workspace";

type AuthFormProps = {
  mode: "login" | "signup";
};

const PASSWORD_MIN_LENGTH = 8; // mirrors the server rule (app.schemas.auth)

// Server-driven error phrasing: the API already returns safe, human strings;
// these branches only sharpen the well-known status codes for this form.
function authErrorMessage(error: unknown, mode: "login" | "signup"): string {
  if (error instanceof ApiError) {
    if (error.status === 401 && mode === "login") return "Invalid email or password.";
    if (error.status === 409) return "An account with this email already exists.";
    if (error.status === 422) return `Enter a valid email and a password of at least ${PASSWORD_MIN_LENGTH} characters.`;
    if (error.status === 429) return "Too many attempts. Please wait a moment and try again.";
    if (error.status === 0) return "The API is not reachable. Start FastAPI on port 8000.";
  }
  return friendlyApiError(error);
}

export function AuthForm({ mode }: AuthFormProps) {
  const router = useRouter();
  const { ready, user, signIn, signUp } = useWorkspace();
  const { showToast } = useToast();
  const [error, setError] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [showPassword, setShowPassword] = useState(false);

  // Already signed in (session cookie still valid): leave the auth pages.
  useEffect(() => {
    if (ready && user) router.replace("/dashboard");
  }, [ready, user, router]);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const form = new FormData(event.currentTarget);
    const email = String(form.get("email") ?? "").trim();
    const password = String(form.get("password") ?? "");
    // Client-side mirror of the server rule (the server enforces it regardless).
    if (mode === "signup" && password.length < PASSWORD_MIN_LENGTH) {
      setError(`Use at least ${PASSWORD_MIN_LENGTH} characters for your password.`);
      return;
    }
    setError("");
    setSubmitting(true);
    try {
      // Real server call: sets the HttpOnly session cookie and returns the user.
      const signedIn = mode === "login" ? await signIn(email, password) : await signUp(email, password);
      showToast(`Welcome, ${signedIn.displayName}. Your workspace is ready.`);
      router.push("/dashboard");
    } catch (caught) {
      setError(authErrorMessage(caught, mode));
    } finally {
      setSubmitting(false);
    }
  }

  const title = mode === "login" ? "Welcome back" : "Create your workspace";
  const description =
    mode === "login"
      ? "Sign in to keep working on your prompts and evaluations."
      : "Create an account — your prompts live in your own workspace.";

  return (
    <div className="grid min-h-screen place-items-center bg-paper px-4 py-10">
      <div className="w-full max-w-[400px]">
        <div className="mb-8 flex justify-center">
          <Brand />
        </div>

        <Card className="p-8">
          <div className="mb-1 flex items-center gap-2 font-mono text-[10px] font-bold tracking-widest text-purple uppercase">
            <Icon name="spark" size={13} />
            {mode === "login" ? "Welcome in" : "Create space"}
          </div>
          <h1 className="text-[22px] font-extrabold tracking-tight text-ink">{title}</h1>
          <p className="mt-1.5 text-[13px] leading-6 text-muted">{description}</p>

          <form onSubmit={onSubmit} className="mt-6" noValidate={false}>
            <label className="block text-xs font-bold text-ink-soft">
              Email address
              <Input name="email" type="email" autoComplete="email" required placeholder="you@example.com" className="mt-1.5" />
            </label>

            <label className="mt-4 block text-xs font-bold text-ink-soft">
              Password
              <span className="relative mt-1.5 block">
                <Input
                  name="password"
                  type={showPassword ? "text" : "password"}
                  minLength={mode === "signup" ? PASSWORD_MIN_LENGTH : undefined}
                  autoComplete={mode === "login" ? "current-password" : "new-password"}
                  required
                  placeholder={mode === "signup" ? `At least ${PASSWORD_MIN_LENGTH} characters` : "Your password"}
                  className="pr-10"
                />
                <button
                  type="button"
                  onClick={() => setShowPassword((current) => !current)}
                  aria-label={showPassword ? "Hide password" : "Show password"}
                  aria-pressed={showPassword}
                  className="absolute inset-y-0 right-0 grid w-10 place-items-center text-faint hover:text-ink"
                >
                  <Icon name={showPassword ? "eye-off" : "eye"} size={17} />
                </button>
              </span>
            </label>

            {error ? (
              <p role="alert" className="mt-3 flex items-center gap-2 rounded-lg bg-danger-soft px-3 py-2 text-xs font-semibold text-danger">
                <Icon name="alert" size={14} className="shrink-0" />
                {error}
              </p>
            ) : null}

            <Button variant="primary" size="lg" icon="spark" type="submit" className="mt-5 w-full" disabled={submitting}>
              {submitting
                ? mode === "login"
                  ? "Signing in…"
                  : "Creating workspace…"
                : mode === "login"
                  ? "Enter SparkPrompt"
                  : "Create workspace"}
            </Button>
          </form>

          <p className="mt-5 text-center text-[12px] text-muted">
            {mode === "login" ? (
              <>
                New here?{" "}
                <Link className="font-bold text-purple hover:underline" href="/signup">
                  Create a workspace
                </Link>
              </>
            ) : (
              <>
                Already have an account?{" "}
                <Link className="font-bold text-purple hover:underline" href="/login">
                  Sign in
                </Link>
              </>
            )}
          </p>
        </Card>

        <p className="mt-4 flex items-start gap-2 rounded-xl bg-purple-faint px-4 py-3 text-[11px] leading-5 text-ink-soft">
          <Icon name="info" size={14} className="mt-0.5 shrink-0 text-purple" />
          <span>
            Passwords are stored as salted scrypt hashes in PostgreSQL. Your session lives in an HttpOnly cookie — never in browser storage.
          </span>
        </p>
      </div>
    </div>
  );
}
