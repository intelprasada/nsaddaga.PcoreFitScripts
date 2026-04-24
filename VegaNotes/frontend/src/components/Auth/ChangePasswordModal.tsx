import { useEffect, useState } from "react";
import { useMutation } from "@tanstack/react-query";
import { api, ApiError } from "../../api/client";

interface Props {
  onClose: () => void;
}

/**
 * Modal for any authenticated user to change their own password.
 * Requires the current password for verification.
 */
export function ChangePasswordModal({ onClose }: Props) {
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [confirm, setConfirm] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [success, setSuccess] = useState(false);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const change = useMutation({
    mutationFn: () => api.changeMyPassword(current, next),
    onSuccess: () => {
      setSuccess(true);
      setErr(null);
      setCurrent(""); setNext(""); setConfirm("");
    },
    onError: (e: any) => {
      if (e instanceof ApiError) setErr(`${e.status}: ${e.detail}`);
      else setErr(String(e?.message ?? e));
    },
  });

  const mismatch = next && confirm && next !== confirm;
  const canSubmit = current && next && confirm && next === confirm && !change.isPending;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center bg-black/40"
      onClick={onClose}
    >
      <div
        className="w-[22rem] rounded-lg bg-white p-5 shadow-xl space-y-4"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between">
          <h2 className="text-sm font-semibold">Change password</h2>
          <button
            onClick={onClose}
            className="text-slate-400 hover:text-slate-700 text-lg leading-none"
            title="Close (Esc)"
          >
            ×
          </button>
        </div>

        {success ? (
          <div className="rounded border border-emerald-200 bg-emerald-50 px-3 py-2 text-sm text-emerald-700">
            Password updated successfully.
            <button
              onClick={onClose}
              className="ml-2 underline text-emerald-800 hover:text-emerald-900"
            >
              Close
            </button>
          </div>
        ) : (
          <form
            className="space-y-3"
            onSubmit={(e) => {
              e.preventDefault();
              if (!canSubmit) return;
              change.mutate();
            }}
          >
            {err && (
              <div className="rounded border border-rose-200 bg-rose-50 px-2 py-1.5 text-xs text-rose-700">
                {err}
              </div>
            )}

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-slate-600">Current password</span>
              <input
                type="password"
                autoFocus
                value={current}
                onChange={(e) => setCurrent(e.target.value)}
                className="rounded border px-2 py-1 text-sm"
                placeholder="current password"
              />
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-slate-600">New password</span>
              <input
                type="password"
                value={next}
                onChange={(e) => setNext(e.target.value)}
                className="rounded border px-2 py-1 text-sm"
                placeholder="new password"
              />
            </label>

            <label className="flex flex-col gap-1 text-xs">
              <span className="text-slate-600">Confirm new password</span>
              <input
                type="password"
                value={confirm}
                onChange={(e) => setConfirm(e.target.value)}
                className={`rounded border px-2 py-1 text-sm ${
                  mismatch ? "border-rose-400" : ""
                }`}
                placeholder="confirm new password"
              />
              {mismatch && (
                <span className="text-rose-600 text-[11px]">Passwords do not match.</span>
              )}
            </label>

            <div className="flex justify-end gap-2 pt-1">
              <button
                type="button"
                onClick={onClose}
                className="rounded border px-3 py-1 text-xs text-slate-600 hover:bg-slate-50"
              >
                Cancel
              </button>
              <button
                type="submit"
                disabled={!canSubmit}
                className="rounded bg-sky-600 text-white px-3 py-1 text-xs disabled:opacity-50"
              >
                {change.isPending ? "Saving…" : "Update password"}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  );
}
