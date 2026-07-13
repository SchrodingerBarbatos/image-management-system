/** Shared terminal / display helpers for batch task statuses. */

export const BATCH_TERMINAL_STATUSES = [
  'done',
  'partial_failed',
  'error',
  'cancelled',
  'interrupted',
] as const;

export type BatchTerminalStatus = (typeof BATCH_TERMINAL_STATUSES)[number];

export function isBatchTerminalStatus(status: string): boolean {
  return (BATCH_TERMINAL_STATUSES as readonly string[]).includes(status);
}

export const BATCH_STATUS_LABELS: Record<string, { color: string; label: string }> = {
  queued: { color: 'default', label: '排队中' },
  running: { color: 'processing', label: '运行中' },
  done: { color: 'success', label: '已完成' },
  partial_failed: { color: 'warning', label: '部分完成' },
  error: { color: 'error', label: '失败' },
  cancelled: { color: 'warning', label: '已取消' },
  interrupted: { color: 'error', label: '中断' },
};
