import { useEffect, useRef, type ReactNode } from 'react';
/** Native modal supplies focus containment, Escape and focus restoration. */
export function Dialog({ titleId, close, children, className = '' }: { titleId: string; close: () => void; children: ReactNode; className?: string }) {
  const ref = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const node = ref.current; const opener = document.activeElement;
    node?.showModal();
    return () => { node?.close(); if (opener instanceof HTMLElement && opener.isConnected) opener.focus(); };
  }, []);
  // 点 ::backdrop 关闭：原生 dialog 的伪元素点击会把事件打在 dialog 自身上，
  // 所以靠 target === node 区分「点背景」和「点内容」。Escape 走 onCancel。
  return <dialog ref={ref} aria-labelledby={titleId} onCancel={event => { event.preventDefault(); close(); }}
    onClick={event => { if (event.target === ref.current) close(); }}
    className={`m-auto max-h-[90dvh] w-[calc(100%-2rem)] overflow-y-auto rounded-2xl border border-primary/30 bg-card p-0 text-foreground shadow-2xl backdrop:bg-black/70 backdrop:backdrop-blur-sm ${className}`}>{children}</dialog>;
}
