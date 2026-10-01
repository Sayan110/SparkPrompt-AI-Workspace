"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";

import { useWorkspace } from "@/lib/workspace";

export default function HomePage() {
  const { ready, user } = useWorkspace();
  const router = useRouter();

  useEffect(() => {
    if (!ready) return;
    router.replace(user ? "/dashboard" : "/login");
  }, [ready, user, router]);

  return <div className="grid min-h-screen place-items-center bg-paper text-sm text-muted">Opening SparkPrompt…</div>;
}
