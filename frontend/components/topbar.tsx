"use client";

import { Icon } from "@/components/icons";
import { useTheme } from "@/lib/theme";

type TopbarProps = {
  title: string;
  description: string;
  onMenu: () => void;
  onOpenCommand: () => void;
};

export function Topbar({ title, description, onMenu, onOpenCommand }: TopbarProps) {
  const { theme, toggleTheme } = useTheme();

  return (
    <header className="sticky top-0 z-20 border-b border-border bg-[color-mix(in_srgb,var(--color-paper)_86%,transparent)] backdrop-blur-md">
      <div className="mx-auto flex h-16 max-w-[1400px] items-center gap-3 px-6 sm:px-8">
        <button
          type="button"
          onClick={onMenu}
          aria-label="Open navigation"
          className="grid h-9 w-9 place-items-center rounded-lg text-ink-soft hover:bg-card dark:hover:bg-raised lg:hidden"
        >
          <Icon name="menu" size={20} />
        </button>

        <div className="min-w-0 flex-1">
          <h1 className="truncate text-[15px] font-extrabold tracking-tight text-ink">{title}</h1>
          <p className="truncate text-[11px] text-faint">{description}</p>
        </div>

        <button
          type="button"
          onClick={onOpenCommand}
          className="hidden h-9 items-center gap-2 rounded-[10px] border border-border-strong bg-card px-3 text-xs font-semibold text-muted hover:border-[#c7bfd9] hover:text-ink sm:flex dark:hover:bg-raised"
        >
          <Icon name="command" size={14} className="text-purple" />
          <span>Commands</span>
          <kbd className="ml-1 hidden text-faint md:inline">⌘K</kbd>
        </button>
        <button
          type="button"
          onClick={onOpenCommand}
          aria-label="Search commands"
          className="grid h-9 w-9 place-items-center rounded-lg text-ink-soft hover:bg-card sm:hidden dark:hover:bg-raised"
        >
          <Icon name="search" size={19} />
        </button>

        <button
          type="button"
          onClick={toggleTheme}
          aria-label={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          title={theme === "dark" ? "Switch to light mode" : "Switch to dark mode"}
          className="grid h-9 w-9 place-items-center rounded-lg text-muted hover:bg-card hover:text-ink dark:hover:bg-raised"
        >
          <Icon name={theme === "dark" ? "sun" : "moon"} size={18} />
        </button>
      </div>
    </header>
  );
}