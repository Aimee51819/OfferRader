import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../api/client";
import { relativeTo } from "../lib/format";

/** Undo the most recent removal of this kind (application or deadline record). */
export default function RecoverButton({ kind }: { kind: "application" | "deadline" }) {
  const qc = useQueryClient();
  const { data } = useQuery({ queryKey: ["trash", kind], queryFn: () => api.trashPeek(kind) });
  const restore = useMutation({
    mutationFn: () => api.trashRestore(kind),
    onSuccess: () => qc.invalidateQueries(),
  });
  const last = data?.last;
  if (!last) return null;
  return (
    <div className="recover">
      {restore.error && <span className="recover-error">{String((restore.error as Error).message)}</span>}
      <button
        className="btn-secondary"
        onClick={() => restore.mutate()}
        disabled={restore.isPending}
        title={`Removed ${relativeTo(last.deleted_at)}${last.count > 1 ? ` · ${last.count - 1} more can be recovered after this` : ""}`}
      >
        ↶ Recover <span className="recover-label">{last.label}</span>
      </button>
    </div>
  );
}
