import Link from "next/link";

import { Icon } from "@/components/icons";

export function Brand({ inverted = false, compact = false }: { inverted?: boolean; compact?: boolean }) {
  return (
    <Link
      href="/dashboard"
      className={`inline-flex items-center gap-2 font-extrabold tracking-tight ${compact ? "text-base" : "text-[17px]"} ${inverted ? "text-white" : "text-ink"}`}
    >
      <span
        className={`grid place-items-center text-white ${compact ? "h-7 w-7 rounded-lg" : "h-8 w-8 rounded-[10px]"} ${
          inverted ? "bg-white text-purple" : "bg-purple shadow-[0_4px_12px_rgba(111,82,217,.32)]"
        }`}
      >
        <Icon name="spark" size={compact ? 15 : 17} />
      </span>
      {!compact ? (
        <span>
          Spark<span className={inverted ? "text-[#f2ebff]" : "text-purple"}>Prompt</span>
        </span>
      ) : null}
    </Link>
  );
}