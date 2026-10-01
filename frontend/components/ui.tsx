"use client";

import { forwardRef, useEffect, type ComponentPropsWithoutRef, type ReactNode } from "react";

import { Icon, type IconName } from "@/components/icons";

/* ------------------------------------------------------------------ */
/* Button                                                              */
/* ------------------------------------------------------------------ */

type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
type ButtonSize = "sm" | "md" | "lg";

const buttonVariants: Record<ButtonVariant, string> = {
  primary:
    "bg-purple text-white shadow-[0_1px_2px_rgba(87,61,190,.28)] hover:bg-purple-dark disabled:bg-[#a99be0] disabled:cursor-not-allowed",
  secondary:
    "border border-border-strong bg-card text-ink-soft hover:bg-paper hover:border-[#c7bfd9] dark:hover:bg-raised disabled:opacity-50 disabled:cursor-not-allowed",
  ghost: "text-muted hover:bg-card hover:text-ink dark:hover:bg-raised disabled:opacity-50",
  danger: "bg-danger text-white hover:brightness-95 disabled:opacity-50",
};

const buttonSizes: Record<ButtonSize, string> = {
  sm: "h-8 px-3 text-xs gap-1.5 rounded-lg",
  md: "h-10 px-4 text-sm gap-2 rounded-[10px]",
  lg: "h-11 px-5 text-sm gap-2 rounded-[10px]",
};

type ButtonProps = ComponentPropsWithoutRef<"button"> & {
  variant?: ButtonVariant;
  size?: ButtonSize;
  icon?: IconName;
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = "secondary", size = "md", icon, className = "", children, type = "button", ...rest },
  ref,
) {
  return (
    <button
      ref={ref}
      type={type}
      className={`inline-flex items-center justify-center font-semibold transition select-none active:scale-[.98] disabled:active:scale-100 ${buttonVariants[variant]} ${buttonSizes[size]} ${className}`}
      {...rest}
    >
      {icon ? <Icon name={icon} size={size === "sm" ? 14 : 16} /> : null}
      {children}
    </button>
  );
});

/* ------------------------------------------------------------------ */
/* Card                                                               */
/* ------------------------------------------------------------------ */

type CardProps = ComponentPropsWithoutRef<"div">;

export function Card({ className = "", ...rest }: CardProps) {
  return (
    <div className={`rounded-2xl border border-border bg-card ${className}`} {...rest} />
  );
}

/* ------------------------------------------------------------------ */
/* Form controls                                                      */
/* ------------------------------------------------------------------ */

export const Input = forwardRef<HTMLInputElement, ComponentPropsWithoutRef<"input">>(function Input(
  { className = "", ...rest },
  ref,
) {
  return (
    <input
      ref={ref}
      className={`h-10 w-full rounded-[10px] border border-border-strong bg-card px-3 text-sm text-ink placeholder:text-faint focus:border-purple focus:outline-none focus:ring-[3px] focus:ring-[#6f52d933] dark:bg-[#201b2b] ${className}`}
      {...rest}
    />
  );
});

export const Textarea = forwardRef<HTMLTextAreaElement, ComponentPropsWithoutRef<"textarea">>(
  function Textarea({ className = "", ...rest }, ref) {
    return (
      <textarea
        ref={ref}
        className={`w-full rounded-[10px] border border-border-strong bg-card px-3 py-2.5 text-sm leading-relaxed text-ink placeholder:text-faint focus:border-purple focus:outline-none focus:ring-[3px] focus:ring-[#6f52d933] dark:bg-[#201b2b] ${className}`}
        {...rest}
      />
    );
  },
);

export const Select = forwardRef<HTMLSelectElement, ComponentPropsWithoutRef<"select">>(
  function Select({ className = "", children, ...rest }, ref) {
    return (
      <select
        ref={ref}
        className={`h-10 w-full appearance-none rounded-[10px] border border-border-strong bg-card px-3 text-sm text-ink focus:border-purple focus:outline-none focus:ring-[3px] focus:ring-[#6f52d933] dark:bg-[#201b2b] ${className}`}
        {...rest}
      >
        {children}
      </select>
    );
  },
);

/* ------------------------------------------------------------------ */
/* Badge                                                              */
/* ------------------------------------------------------------------ */

type BadgeTone = "purple" | "neutral" | "success" | "danger" | "warning";

const badgeTones: Record<BadgeTone, string> = {
  purple: "bg-purple-soft text-purple-dark dark:text-[#cbbcff]",
  neutral: "bg-paper text-muted border border-border dark:bg-raised",
  success: "bg-success-soft text-success",
  danger: "bg-danger-soft text-danger",
  warning: "bg-yellow text-[#7a6216] dark:text-[#e8d18a]",
};

export function Badge({ tone = "neutral", className = "", children }: { tone?: BadgeTone; className?: string; children: ReactNode }) {
  return (
    <span className={`inline-flex items-center gap-1 rounded-md px-2 py-0.5 text-[11px] font-semibold ${badgeTones[tone]} ${className}`}>
      {children}
    </span>
  );
}

/* ------------------------------------------------------------------ */
/* Skeleton                                                           */
/* ------------------------------------------------------------------ */

export function Skeleton({ className = "" }: { className?: string }) {
  return <div className={`animate-pulse rounded-md bg-[#e9e4f1] dark:bg-[#2e2739] ${className}`} aria-hidden />;
}

/* ------------------------------------------------------------------ */
/* Modal / ConfirmDialog                                              */
/* ------------------------------------------------------------------ */

type ModalProps = {
  open: boolean;
  onClose: () => void;
  title?: string;
  describe?: string;
  children: ReactNode;
  labelledBy?: string;
};

export function Modal({ open, onClose, title, describe, children, labelledBy }: ModalProps) {
  useEffect(() => {
    if (!open) return;
    function onKey(event: KeyboardEvent) {
      if (event.key === "Escape") onClose();
    }
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
    };
  }, [open, onClose]);

  if (!open) return null;

  return (
    <div
      role="presentation"
      className="fixed inset-0 z-50 grid place-items-center p-4"
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="absolute inset-0 bg-overlay backdrop-blur-[2px]" aria-hidden />
      <div
        role="dialog"
        aria-modal="true"
        aria-label={title}
        aria-describedby={describe}
        className="relative w-full max-w-md rounded-2xl border border-border bg-card p-6 shadow-[0_24px_60px_rgba(20,14,40,.28)]"
      >
        {title ? (
          <div className="mb-4 flex items-center justify-between">
            <h2 id={labelledBy} className="text-base font-bold text-ink">
              {title}
            </h2>
            <button
              type="button"
              onClick={onClose}
              aria-label="Close dialog"
              className="grid h-8 w-8 place-items-center rounded-lg text-faint hover:bg-paper hover:text-ink dark:hover:bg-raised"
            >
              <Icon name="x" size={18} />
            </button>
          </div>
        ) : null}
        {children}
      </div>
    </div>
  );
}

export function ConfirmDialog({
  open,
  onClose,
  onConfirm,
  title,
  body,
  confirmLabel = "Delete",
  busy = false,
  // Phase 3J: the dialog is reused for version restore, which is a creation, not a
  // deletion. Defaults preserve the existing delete appearance for current callers.
  tone = "danger",
  icon = "trash",
  busyLabel = "Deleting…",
}: {
  open: boolean;
  onClose: () => void;
  onConfirm: () => void;
  title: string;
  body: string;
  confirmLabel?: string;
  busy?: boolean;
  tone?: "danger" | "primary";
  icon?: IconName;
  busyLabel?: string;
}) {
  return (
    <Modal open={open} onClose={onClose} title={title}>
      <p className="text-sm leading-6 text-muted">{body}</p>
      <div className="mt-6 flex justify-end gap-2">
        <Button variant="secondary" size="sm" onClick={onClose} disabled={busy}>
          Cancel
        </Button>
        <Button variant={tone} size="sm" onClick={onConfirm} disabled={busy} icon={icon}>
          {busy ? busyLabel : confirmLabel}
        </Button>
      </div>
    </Modal>
  );
}

/* ------------------------------------------------------------------ */
/* Headers                                                            */
/* ------------------------------------------------------------------ */

export function PageHeader({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: ReactNode;
  description?: string;
  actions?: ReactNode;
}) {
  return (
    <div className="flex flex-wrap items-start justify-between gap-4">
      <div className="min-w-0 max-w-2xl">
        {eyebrow ? <p className="text-[11px] font-bold tracking-[1.6px] text-purple uppercase">{eyebrow}</p> : null}
        <h1 className="mt-2 text-[26px] leading-tight font-extrabold tracking-tight text-ink sm:text-[32px]">{title}</h1>
        {description ? <p className="mt-2 text-sm leading-6 text-muted">{description}</p> : null}
      </div>
      {actions ? <div className="flex shrink-0 items-center gap-2">{actions}</div> : null}
    </div>
  );
}

export function SectionHeader({
  title,
  hint,
  action,
}: {
  title: ReactNode;
  hint?: ReactNode;
  action?: ReactNode;
}) {
  return (
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
      <h2 className="flex items-center gap-2 text-[13px] font-bold text-ink-soft">
        <span className="h-4 w-1 rounded-full bg-purple" aria-hidden />
        {title}
      </h2>
      <div className="flex items-center gap-3">
        {hint ? <span className="text-xs text-faint">{hint}</span> : null}
        {action}
      </div>
    </div>
  );
}