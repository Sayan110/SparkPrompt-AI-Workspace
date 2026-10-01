"use client";

import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";

import { api, setUnauthorizedHandler, type ApiSessionUser } from "@/lib/api";
import type { SessionUser, SparkEntry } from "@/lib/types";
import { newId } from "@/lib/utils";

type WorkspaceContextValue = {
  ready: boolean;
  user: SessionUser | null;
  sparks: SparkEntry[];
  signIn: (email: string, password: string) => Promise<SessionUser>;
  signUp: (email: string, password: string) => Promise<SessionUser>;
  signOut: () => Promise<void>;
  addSpark: (entry: Omit<SparkEntry, "id" | "createdAt">) => void;
  clearSparks: () => void;
};

const WorkspaceContext = createContext<WorkspaceContextValue | null>(null);

// Server record -> UI shape. The only identity the client ever holds; the real
// session lives in an HttpOnly cookie that JavaScript cannot read.
function toSessionUser(record: ApiSessionUser): SessionUser {
  return { id: record.id, email: record.email, displayName: record.display_name };
}

export function WorkspaceProvider({ children }: { children: ReactNode }) {
  const [ready, setReady] = useState(false);
  const [user, setUser] = useState<SessionUser | null>(null);
  const [sparks, setSparks] = useState<SparkEntry[]>([]);

  // Boot probe: who does the server say we are? A 401 (no or expired session)
  // and an unreachable API both resolve to "not signed in" here so the login
  // gate can render; the next navigation re-probes.
  useEffect(() => {
    let cancelled = false;
    api
      .getSession()
      .then((record) => {
        if (!cancelled) setUser(toSessionUser(record));
      })
      .catch(() => {
        if (!cancelled) setUser(null);
      })
      .finally(() => {
        if (!cancelled) setReady(true);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // Any 401 anywhere in the app clears the local user; AppShell then redirects
  // to /login. Registered once at mount (login page has no AppShell -> no loop).
  useEffect(() => {
    setUnauthorizedHandler(() => setUser(null));
    return () => setUnauthorizedHandler(null);
  }, []);

  const signIn = useCallback(async (email: string, password: string): Promise<SessionUser> => {
    const record = await api.login({ email, password });
    const next = toSessionUser(record);
    setUser(next);
    return next;
  }, []);

  const signUp = useCallback(async (email: string, password: string): Promise<SessionUser> => {
    const record = await api.signup({ email, password });
    const next = toSessionUser(record);
    setUser(next);
    return next;
  }, []);

  const signOut = useCallback(async () => {
    try {
      // Server revokes the token and clears the cookie.
      await api.logout();
    } catch {
      // API unreachable: clear locally anyway so the UI can never get stuck.
    }
    setUser(null);
    setSparks([]);
  }, []);

  const addSpark = useCallback((entry: Omit<SparkEntry, "id" | "createdAt">) => {
    setSparks((current) =>
      [{ ...entry, id: newId(), createdAt: Date.now() }, ...current.filter((item) => item.idea !== entry.idea)].slice(0, 8),
    );
  }, []);

  const clearSparks = useCallback(() => setSparks([]), []);

  const value = useMemo(
    () => ({ ready, user, sparks, signIn, signUp, signOut, addSpark, clearSparks }),
    [ready, user, sparks, signIn, signUp, signOut, addSpark, clearSparks],
  );

  return <WorkspaceContext.Provider value={value}>{children}</WorkspaceContext.Provider>;
}

export function useWorkspace(): WorkspaceContextValue {
  const context = useContext(WorkspaceContext);
  if (!context) throw new Error("useWorkspace must be used inside WorkspaceProvider");
  return context;
}
