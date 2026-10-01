"use client";

import Link from "next/link";
import { usePathname, useRouter } from "next/navigation";

import { Brand } from "@/components/brand";
import { Icon } from "@/components/icons";
import { navItems } from "@/lib/nav";
import { useTheme } from "@/lib/theme";
import { useWorkspace } from "@/lib/workspace";

export function Sidebar({
  open,
  onClose,
  collapsed,
  onToggleCollapse,
}: {
  open: boolean;
  onClose: () => void;
  collapsed: boolean;
  onToggleCollapse: () => void;
}) {
  const pathname = usePathname();
  const router = useRouter();
  const { theme, toggleTheme } = useTheme();
  const { user, signOut } = useWorkspace();
  const isDark = theme === "dark";

  const initials = (user?.displayName ?? "Account")
    .split(" ")
    .map((part) => part.charAt(0))
    .slice(0, 2)
    .join("")
    .toUpperCase();

  return (
    <>
      {open ? (
        <button
          type="button"
          aria-label="Close navigation"
          className="fixed inset-0 z-30 bg-overlay lg:hidden"
          onClick={onClose}
        />
      ) : null}
      <aside
        className={`fixed inset-y-0 left-0 z-40 flex flex-col border-r border-border bg-sidebar transition-[width,transform] duration-200 lg:translate-x-0 ${
          collapsed ? "lg:w-[68px]" : "lg:w-[242px]"
        } ${open ? "w-[242px] translate-x-0 shadow-[12px_0_40px_rgba(30,20,60,.18)]" : "w-[242px] -translate-x-full"}`}
        aria-label="Workspace navigation"
      >
        <div className={`flex h-16 shrink-0 items-center ${collapsed ? "lg:justify-center lg:px-0" : "px-5"}`}>
          {collapsed ? <Brand compact /> : <Brand />}
          <button
            type="button"
            onClick={onClose}
            aria-label="Close navigation"
            className="ml-auto grid h-8 w-8 place-items-center rounded-lg text-faint hover:bg-card hover:text-ink lg:hidden"
          >
            <Icon name="x" size={18} />
          </button>
        </div>

        <div className={`mx-4 mb-4 hidden rounded-xl border border-border bg-card px-3 py-2.5 lg:block ${collapsed ? "lg:hidden" : ""}`}>
          <p className="truncate text-[11px] font-bold text-ink-soft">Personal workspace</p>
          <p className="truncate text-[10px] text-faint">{user?.email ?? "Signed in"}</p>
        </div>

        <nav className="flex-1 overflow-y-auto px-3 pb-4" aria-label="Main navigation">
          <ul className="grid gap-1">
            {navItems.map((item) => {
              const active = pathname === item.href;
              return (
                <li key={item.href}>
                  <Link
                    href={item.href}
                    onClick={onClose}
                    title={collapsed ? item.label : undefined}
                    aria-current={active ? "page" : undefined}
                    className={`group relative flex items-center gap-3 rounded-[10px] px-3 text-[13px] transition ${
                      collapsed ? "lg:justify-center lg:px-0" : ""
                    } ${active ? "bg-purple-soft font-bold text-purple-dark dark:text-[#d3c5ff]" : "font-semibold text-muted hover:bg-card hover:text-ink dark:hover:bg-raised"}`}
                  >
                    {active ? <span className="absolute top-1/2 left-0 h-5 w-[3px] -translate-y-1/2 rounded-r-full bg-purple" aria-hidden /> : null}
                    <span className={`grid h-9 w-9 shrink-0 place-items-center rounded-lg ${active ? "text-purple" : "text-faint group-hover:text-ink-soft dark:group-hover:text-ink"}`}>
                      <Icon name={item.icon} size={18} />
                    </span>
                    <span className={collapsed ? "lg:hidden" : ""}>{item.label}</span>
                  </Link>
                </li>
              );
            })}
          </ul>
        </nav>

        <div className="shrink-0 border-t border-border px-3 py-3">
          <div className={`mb-2 flex items-center gap-2.5 rounded-[10px] px-2 py-1.5 ${collapsed ? "lg:justify-center lg:px-0" : ""}`}>
            <span className="grid h-8 w-8 shrink-0 place-items-center rounded-full bg-purple-soft text-[11px] font-extrabold text-purple">
              {initials}
            </span>
            {!collapsed ? (
              <div className="min-w-0 flex-1">
                <p className="truncate text-xs font-bold text-ink">{user?.displayName ?? "Account"}</p>
                <p className="truncate text-[10px] text-faint">Signed in</p>
              </div>
            ) : null}
          </div>

          <div className={`flex items-center gap-1 ${collapsed ? "lg:flex-col" : ""}`}>
            <button
              type="button"
              onClick={toggleTheme}
              title={isDark ? "Switch to light mode" : "Switch to dark mode"}
              className={`flex items-center gap-2 rounded-lg text-xs font-semibold text-muted transition hover:bg-card hover:text-ink dark:hover:bg-raised ${
                collapsed ? "lg:h-9 lg:w-9 lg:justify-center" : "px-2.5 py-2"
              }`}
            >
              <Icon name={isDark ? "sun" : "moon"} size={16} />
              {!collapsed ? <span>{isDark ? "Light mode" : "Dark mode"}</span> : null}
            </button>
            <button
              type="button"
              onClick={async () => {
                // Server-side revocation first; signOut never throws, so the
                // redirect always runs (and AppShell's gate is a backstop).
                await signOut();
                router.push("/login");
              }}
              title="Sign out"
              className={`flex items-center gap-2 rounded-lg text-xs font-semibold text-muted transition hover:bg-card hover:text-danger dark:hover:bg-raised ${
                collapsed ? "lg:h-9 lg:w-9 lg:justify-center" : "px-2.5 py-2"
              }`}
            >
              <Icon name="open" size={16} className="rotate-180" />
              {!collapsed ? <span>Sign out</span> : null}
            </button>
            <button
              type="button"
              onClick={onToggleCollapse}
              title={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              aria-label={collapsed ? "Expand sidebar" : "Collapse sidebar"}
              className={`hidden items-center gap-2 rounded-lg text-xs font-semibold text-muted transition hover:bg-card hover:text-ink dark:hover:bg-raised lg:flex ${
                collapsed ? "lg:h-9 lg:w-9 lg:justify-center" : "px-2.5 py-2"
              }`}
            >
              <Icon name={collapsed ? "chevron-right" : "chevron-left"} size={16} />
              {!collapsed ? <span>Collapse</span> : null}
            </button>
          </div>
        </div>
      </aside>
    </>
  );
}