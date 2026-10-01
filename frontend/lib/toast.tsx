"use client";

import { createContext, useCallback, useContext, useMemo, useRef, useState, type ReactNode } from "react";

import { Icon, type IconName } from "@/components/icons";

export type ToastType = "success" | "error" | "info";

type ToastItem = {
  id: number;
  message: string;
  type: ToastType;
};

type ToastContextValue = {
  showToast: (message: string, type?: ToastType) => void;
};

const ToastContext = createContext<ToastContextValue | null>(null);

const toastStyles: Record<ToastType, { icon: IconName; className: string }> = {
  success: { icon: "check", className: "border-[#cdeedd] dark:border-[#2c4d3b]" },
  error: { icon: "alert", className: "border-[#f0cfcf] dark:border-[#58303a]" },
  info: { icon: "info", className: "border-[#dcd3f3] dark:border-[#3d3257]" },
};

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<ToastItem[]>([]);
  const counter = useRef(0);

  const showToast = useCallback((message: string, type: ToastType = "success") => {
    counter.current += 1;
    const id = counter.current;
    setToasts((current) => [...current.slice(-2), { id, message, type }]);
    window.setTimeout(() => setToasts((current) => current.filter((item) => item.id !== id)), 2600);
  }, []);

  const value = useMemo(() => ({ showToast }), [showToast]);

  return (
    <ToastContext.Provider value={value}>
      {children}
      <div
        aria-live="polite"
        aria-atomic="false"
        className="pointer-events-none fixed right-4 bottom-4 z-[60] flex w-[calc(100%-2rem)] max-w-sm flex-col gap-2"
      >
        {toasts.map((toast) => {
          const style = toastStyles[toast.type];
          return (
            <div
              key={toast.id}
              role="status"
              className={`flex items-start gap-2.5 rounded-xl border bg-card px-3.5 py-3 shadow-[0_10px_30px_rgba(20,14,40,.18)] ${style.className}`}
            >
              <span className={`mt-0.5 shrink-0 ${toast.type === "success" ? "text-success" : toast.type === "error" ? "text-danger" : "text-purple"}`}>
                <Icon name={style.icon} size={16} />
              </span>
              <p className="text-[13px] leading-5 font-semibold text-ink">{toast.message}</p>
            </div>
          );
        })}
      </div>
    </ToastContext.Provider>
  );
}

export function useToast(): ToastContextValue {
  const context = useContext(ToastContext);
  if (!context) throw new Error("useToast must be used inside ToastProvider");
  return context;
}