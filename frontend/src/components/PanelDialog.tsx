import { useEffect, useRef, type ReactNode } from "react";
import { ArrowLeft } from "lucide-react";
import { Button } from "./ui/button";

export function PanelDialog({ title, onClose, children }: { title: string; onClose: () => void; children: ReactNode }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement;
    const dialog = ref.current!;
    dialog.showModal();
    dialog.querySelector<HTMLElement>("[data-panel-autofocus]")?.focus();
    return () => { dialog.close(); if (previous instanceof HTMLElement && previous.isConnected) previous.focus(); };
  }, []);
  return <dialog ref={ref} aria-label={title} className="panel-dialog" onCancel={event => { event.preventDefault(); onClose(); }} onKeyDown={event => { if (event.key === "Escape") { event.preventDefault(); event.stopPropagation(); onClose(); } }}>
    <header className="flex h-14 shrink-0 items-center gap-3 border-b border-border px-4">
      <Button variant="ghost" size="icon" className="size-8" aria-label="返回对话" title="返回对话" onClick={onClose}><ArrowLeft size={18} /></Button>
      <h2 className="text-sm font-semibold">{title}</h2>
    </header>
    {children}
  </dialog>;
}
