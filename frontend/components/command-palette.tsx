"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { usePathname, useRouter } from "next/navigation";

import { Icon } from "@/components/icons";
import { navItems } from "@/lib/nav";

export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange: (next: boolean) => void }) {
  const [query, setQuery] = useState("");
  const [activeIndex, setActiveIndex] = useState(0);
  const router = useRouter();
  const pathname = usePathname();
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLUListElement>(null);

  useEffect(() => {
    if (!open) return;
    setQuery("");
    setActiveIndex(0);
    requestAnimationFrame(() => inputRef.current?.focus());
  }, [open]);

  useEffect(() => {
    function onKey(event: KeyboardEvent) {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k") {
        event.preventDefault();
        onOpenChange(!open);
      }
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [open, onOpenChange]);

  useEffect(() => {
    if (!open) return;
    function onEscape(event: KeyboardEvent) {
      if (event.key === "Escape") onOpenChange(false);
    }
    document.addEventListener("keydown", onEscape);
    return () => document.removeEventListener("keydown", onEscape);
  }, [open, onOpenChange]);

  const filtered = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const items = needle
      ? navItems.filter(
          (item) =>
            item.label.toLowerCase().includes(needle) ||
            item.description.toLowerCase().includes(needle) ||
            item.href.includes(needle),
        )
      : navItems;
    return items.map((item) => ({ ...item, current: item.href === pathname }));
  }, [query, pathname]);

  function go(index: number) {
    const item = filtered[index];
    if (!item) return;
    router.push(item.href);
    onOpenChange(false);
  }

  function onKeyDown(event: React.KeyboardEvent) {
    if (event.key === "ArrowDown") {
      event.preventDefault();
      setActiveIndex((current) => (current + 1) % Math.max(filtered.length, 1));
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      setActiveIndex((current) => (current - 1 + Math.max(filtered.length, 1)) % Math.max(filtered.length, 1));
    } else if (event.key === "Enter") {
      event.preventDefault();
      go(activeIndex);
    }
  }

  useEffect(() => {
    const item = listRef.current?.querySelector<HTMLElement>(`[data-index="${activeIndex}"]`);
    item?.scrollIntoView({ block: "nearest" });
  }, [activeIndex]);

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-[70] grid place-items-start px-4 pt-[12vh]" role="presentation">
      <div className="absolute inset-0 bg-overlay backdrop-blur-[2px]" onClick={() => onOpenChange(false)} aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label="Command palette"
        className="relative w-full max-w-lg overflow-hidden rounded-2xl border border-border bg-card shadow-[0_28px_70px_rgba(20,14,40,.32)]"
      >
        <div className="flex items-center gap-2 border-b border-border px-3">
          <Icon name="search" size={17} className="shrink-0 text-faint" />
          <input
            ref={inputRef}
            role="combobox"
            aria-expanded="true"
            aria-controls="command-list"
            aria-activedescendant={filtered[activeIndex] ? `command-${filtered[activeIndex].href}` : undefined}
            value={query}
            onChange={(event) => {
              setQuery(event.target.value);
              setActiveIndex(0);
            }}
            onKeyDown={onKeyDown}
            placeholder="Search pages…"
            className="h-12 w-full bg-transparent text-sm text-ink placeholder:text-faint focus:outline-none"
          />
          <kbd className="shrink-0 text-faint">esc</kbd>
        </div>
        <ul ref={listRef} id="command-list" role="listbox" aria-label="Navigation results" className="max-h-[40vh] overflow-y-auto p-2">
          {filtered.length ? (
            filtered.map((item, index) => (
              <li key={item.href} role="option" id={`command-${item.href}`} aria-selected={index === activeIndex} data-index={index}>
                <button
                  type="button"
                  onMouseEnter={() => setActiveIndex(index)}
                  onClick={() => go(index)}
                  className={`flex w-full items-center gap-3 rounded-[10px] px-3 py-2.5 text-left text-[13px] ${
                    index === activeIndex ? "bg-purple-soft text-purple-dark dark:text-[#d3c5ff]" : "text-ink"
                  }`}
                >
                  <span className={`grid h-8 w-8 shrink-0 place-items-center rounded-lg ${index === activeIndex ? "text-purple" : "text-faint"}`}>
                    <Icon name={item.icon} size={17} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-semibold">{item.label}</span>
                    <span className="block truncate text-[11px] text-faint">{item.description}</span>
                  </span>
                  {item.current ? <span className="shrink-0 text-[10px] font-bold text-purple">Current</span> : null}
                  {index === activeIndex ? <Icon name="chevron-right" size={15} className="shrink-0 text-purple" /> : null}
                </button>
              </li>
            ))
          ) : (
            <li className="px-3 py-8 text-center text-sm text-muted">No pages match “{query}”.</li>
          )}
        </ul>
        <div className="flex items-center gap-3 border-t border-border bg-paper px-4 py-2 text-[10px] text-faint dark:bg-raised">
          <span className="flex items-center gap-1">
            <kbd>↑</kbd>
            <kbd>↓</kbd> navigate
          </span>
          <span className="flex items-center gap-1">
            <kbd>↵</kbd> open
          </span>
          <span className="ml-auto">Ctrl / ⌘ + K</span>
        </div>
      </div>
    </div>
  );
}