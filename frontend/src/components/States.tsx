import type { ReactNode } from "react";

export function Loading({ what = "Loading" }: { what?: string }) {
  return (
    <div className="empty" role="status" aria-live="polite">
      {what}…
    </div>
  );
}

export function ErrorBox({ error, hint }: { error: unknown; hint?: ReactNode }) {
  const msg = error instanceof Error ? error.message : String(error);
  return (
    <div className="alert alert-error" role="alert">
      <strong>Something went wrong.</strong> {msg}
      {hint && <div className="muted">{hint}</div>}
    </div>
  );
}

export function Empty({ children }: { children: ReactNode }) {
  return <div className="empty">{children}</div>;
}
