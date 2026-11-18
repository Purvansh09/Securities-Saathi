import { useEffect, useState, type ReactNode } from "react";

interface Props {
  /** The button as it first appears. */
  children: ReactNode;
  /** The question shown once it's clicked, next to Yes / Cancel. */
  question: ReactNode;
  confirmLabel?: string;
  onConfirm: () => void;
  className?: string;
  disabled?: boolean;
  danger?: boolean;
}

/** A two-step button: the first click asks, the second acts. Used instead of window.confirm(),
 *  which some embedded browsers (including the Claude desktop app's browser pane) cancel silently
 *  without showing a dialog -- which made Import, Discard and Delete do nothing there. */
export function ConfirmButton({ children, question, confirmLabel = "Yes", onConfirm, className = "btn btn-ghost", disabled, danger }: Props) {
  const [asking, setAsking] = useState(false);

  // Escape backs out, like a dialog would.
  useEffect(() => {
    if (!asking) return;
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && setAsking(false);
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [asking]);

  if (!asking) {
    return (
      <button type="button" className={className} disabled={disabled} onClick={() => setAsking(true)}>
        {children}
      </button>
    );
  }
  return (
    <span className="confirm-inline" role="group" aria-live="polite">
      <span className="confirm-question">{question}</span>
      <button
        type="button"
        className={`btn btn-sm ${danger ? "btn-danger" : "btn-primary"}`}
        autoFocus
        onClick={() => {
          setAsking(false);
          onConfirm();
        }}
      >
        {confirmLabel}
      </button>
      <button type="button" className="btn btn-ghost btn-sm" onClick={() => setAsking(false)}>
        Cancel
      </button>
    </span>
  );
}
