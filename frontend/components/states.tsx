import type { ReactNode } from "react";

import { Icon, type IconName } from "@/components/icons";
import { Button, Skeleton } from "@/components/ui";

export function EmptyState({
  icon = "spark",
  title,
  body,
  action,
}: {
  icon?: IconName;
  title: string;
  body: string;
  action?: ReactNode;
}) {
  return (
    <div className="grid min-h-[240px] place-items-center py-10 text-center">
      <div className="max-w-sm">
        <div className="mx-auto mb-4 grid h-12 w-12 place-items-center rounded-2xl bg-purple-soft text-purple">
          <Icon name={icon} size={22} />
        </div>
        <h3 className="text-sm font-bold text-ink">{title}</h3>
        <p className="mx-auto mt-1.5 text-[13px] leading-6 text-muted">{body}</p>
        {action ? <div className="mt-5 flex justify-center">{action}</div> : null}
      </div>
    </div>
  );
}

export function ErrorState({
  title = "Something went wrong",
  message,
  retry,
  action,
}: {
  title?: string;
  message: string;
  retry?: () => void;
  action?: ReactNode;
}) {
  return (
    <div className="flex items-start gap-3 rounded-xl border border-[#ecc9c9] bg-danger-soft p-4 dark:border-[#59303a]">
      <span className="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-lg bg-danger/10 text-danger">
        <Icon name="alert" size={17} />
      </span>
      <div className="min-w-0 flex-1">
        <p className="text-sm font-bold text-danger">{title}</p>
        <p className="mt-0.5 text-[13px] leading-5 text-[#8a3d41] dark:text-[#f0c7cc]">{message}</p>
        {retry || action ? (
          <div className="mt-3 flex flex-wrap gap-2">
            {retry ? (
              <Button variant="secondary" size="sm" icon="refresh" onClick={retry}>
                Try again
              </Button>
            ) : null}
            {action}
          </div>
        ) : null}
      </div>
    </div>
  );
}

export function ListSkeleton({ rows = 3 }: { rows?: number }) {
  return (
    <div className="grid gap-2" aria-label="Loading">
      {Array.from({ length: rows }).map((_, index) => (
        <div key={index} className="rounded-xl border border-border bg-card p-4">
          <div className="flex items-center gap-3">
            <Skeleton className="h-9 w-9 rounded-lg" />
            <div className="flex-1">
              <Skeleton className="h-3.5 w-1/3" />
              <Skeleton className="mt-2 h-3 w-2/3" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}

export function CardSkeleton({ count = 4 }: { count?: number }) {
  return (
    <div className="grid gap-4 md:grid-cols-2">
      {Array.from({ length: count }).map((_, index) => (
        <div key={index} className="rounded-2xl border border-border bg-card p-6">
          <div className="flex items-start gap-3">
            <Skeleton className="h-10 w-10 rounded-xl" />
            <div className="flex-1">
              <Skeleton className="h-4 w-1/2" />
              <Skeleton className="mt-2.5 h-3 w-full" />
              <Skeleton className="mt-2 h-3 w-3/4" />
            </div>
          </div>
        </div>
      ))}
    </div>
  );
}