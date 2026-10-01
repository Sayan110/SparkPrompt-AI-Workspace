"use client";

import { useEffect, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { CommandPalette } from "@/components/command-palette";
import { Sidebar } from "@/components/sidebar";
import { Topbar } from "@/components/topbar";
import { navItemForPath } from "@/lib/nav";
import { useWorkspace } from "@/lib/workspace";

const COLLAPSE_KEY = "sparkprompt-sidebar-collapsed";

export function AppShell({ children }: { children: React.ReactNode }) {
  const { ready, user } = useWorkspace();
  const router = useRouter();
  const pathname = usePathname();
  const [menuOpen, setMenuOpen] = useState(false);
  const [paletteOpen, setPaletteOpen] = useState(false);
  const [collapsed, setCollapsed] = useState(false);

  const nav = navItemForPath(pathname);

  useEffect(() => {
    if (ready && !user) router.replace("/login");
  }, [ready, user, router]);

  useEffect(() => {
    setMenuOpen(false);
  }, [pathname]);

  useEffect(() => {
    try {
      setCollapsed(localStorage.getItem(COLLAPSE_KEY) === "1");
    } catch {
      /* ignore */
    }
  }, []);

  function toggleCollapse() {
    setCollapsed((current) => {
      const next = !current;
      try {
        localStorage.setItem(COLLAPSE_KEY, next ? "1" : "0");
      } catch {
        /* ignore */
      }
      return next;
    });
  }

  if (!ready || !user) {
    return (
      <div className="grid min-h-screen place-items-center bg-paper text-sm text-muted" role="status">
        Loading workspace…
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-paper">
      <Sidebar open={menuOpen} onClose={() => setMenuOpen(false)} collapsed={collapsed} onToggleCollapse={toggleCollapse} />
      <div className={`flex min-h-screen flex-col transition-[padding] duration-200 ${collapsed ? "lg:pl-[68px]" : "lg:pl-[242px]"}`}>
        <Topbar
          title={nav?.label ?? "SparkPrompt"}
          description={nav?.description ?? ""}
          onMenu={() => setMenuOpen((current) => !current)}
          onOpenCommand={() => setPaletteOpen(true)}
        />
        <main className="flex-1 pb-16">{children}</main>
      </div>
      <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />
    </div>
  );
}