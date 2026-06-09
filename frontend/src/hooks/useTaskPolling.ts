import { useState, useRef, useEffect, useCallback } from 'react';
import { message } from 'antd';
import { taskApi, BatchTaskInfo } from '../services/api';

export interface UseTaskPollingOptions {
  onComplete?: (task: BatchTaskInfo) => void;
  onError?: (task: BatchTaskInfo) => void;
  successMessage?: string | ((task: BatchTaskInfo) => string);
  errorMessage?: string | ((task: BatchTaskInfo) => string);
  pollInterval?: number;
}

export interface UseTaskPollingReturn {
  taskId: number | null;
  taskStatus: string;
  currentTask: BatchTaskInfo | null;
  polling: boolean;
  startPolling: (taskId: number) => void;
  cancelPolling: () => void;
  reset: () => void;
}

export function useTaskPolling(options: UseTaskPollingOptions = {}): UseTaskPollingReturn {
  const {
    onComplete,
    onError,
    successMessage,
    errorMessage,
    pollInterval = 2000,
  } = options;

  const [taskId, setTaskId] = useState<number | null>(null);
  const [taskStatus, setTaskStatus] = useState<string>('');
  const [currentTask, setCurrentTask] = useState<BatchTaskInfo | null>(null);
  const [polling, setPolling] = useState(false);
  const pollTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const cancelPolling = useCallback(() => {
    if (pollTimerRef.current) {
      clearTimeout(pollTimerRef.current);
      pollTimerRef.current = null;
    }
    setPolling(false);
  }, []);

  const poll = useCallback((id: number) => {
    cancelPolling();
    setPolling(true);
    taskApi.getTask(id).then(task => {
      setTaskStatus(task.status);
      setCurrentTask(task);
      if (task.status === 'running' || task.status === 'queued') {
        pollTimerRef.current = setTimeout(() => poll(id), pollInterval);
      } else {
        setPolling(false);
        if (task.status === 'done') {
          const msg = typeof successMessage === 'function'
            ? successMessage(task)
            : successMessage || `任务完成，共处理 ${task.result_count} 项`;
          message.success(msg);
          onComplete?.(task);
        } else if (task.status === 'error' || task.status === 'cancelled' || task.status === 'interrupted') {
          const defaultMessage = task.status === 'cancelled'
            ? '任务已取消'
            : task.status === 'interrupted'
              ? '任务已中断'
              : task.error_message || '任务失败';
          const msg = typeof errorMessage === 'function'
            ? errorMessage(task)
            : errorMessage || defaultMessage;
          message.error(msg);
          onError?.(task);
        }
      }
    }).catch(() => {
      setPolling(false);
    });
  }, [cancelPolling, pollInterval, successMessage, errorMessage, onComplete, onError]);

  const startPolling = useCallback((id: number) => {
    setTaskId(id);
    poll(id);
  }, [poll]);

  const reset = useCallback(() => {
    cancelPolling();
    setTaskId(null);
    setTaskStatus('');
    setCurrentTask(null);
  }, [cancelPolling]);

  // Cleanup on unmount
  useEffect(() => {
    return () => {
      if (pollTimerRef.current) clearTimeout(pollTimerRef.current);
    };
  }, []);

  return {
    taskId,
    taskStatus,
    currentTask,
    polling,
    startPolling,
    cancelPolling,
    reset,
  };
}
