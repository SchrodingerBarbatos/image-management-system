import React, { useState } from 'react';
import { Button, App } from 'antd';
import { BatchTaskInfo, taskApi } from '../services/api';
import { fmtEta } from '../utils/format';
import { BATCH_STATUS_LABELS, isBatchTerminalStatus } from '../utils/taskStatus';
import { Led, LightBar } from './ui';

const STATUS_LED: Record<string, 'amber' | 'blue' | 'green' | 'red' | undefined> = {
  queued: 'amber',
  running: 'blue',
  done: 'green',
  partial_failed: 'amber',
  error: 'red',
  cancelled: undefined,
  interrupted: 'red',
};

export function TaskStatusBadge({ status }: { status: string }) {
  const cfg = BATCH_STATUS_LABELS[status] || { color: 'default', label: status };
  return (
    <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontSize: 12 }}>
      <Led color={STATUS_LED[status]} />
      {cfg.label}
    </span>
  );
}

export function TaskProgress({ task }: { task: BatchTaskInfo }) {
  const [showFailed, setShowFailed] = useState(false);

  if (task.status === 'queued') {
    return <span className="hint">排队中…</span>;
  }
  if (task.status === 'running') {
    const percent = task.percent || (task.total > 0 ? Math.round((task.progress / task.total) * 100) : 0);
    return (
      <div>
        <LightBar value={percent / 100} />
        <div className="mono" style={{ fontSize: 10.5, color: 'var(--t3)', marginTop: 6 }}>
          {task.progress}/{task.total}
          {task.current_item && ` | 当前: ${task.current_item}`}
          {task.speed > 0 && ` | 速度: ${task.speed}/秒`}
          {task.eta_seconds > 0 && ` | 剩余: ${fmtEta(task.eta_seconds)}`}
          {task.failed_count > 0 && ` | 失败: ${task.failed_count}`}
        </div>
      </div>
    );
  }
  if (task.status === 'done' || task.status === 'partial_failed') {
    const elapsed = task.elapsed_seconds;
    const failed = task.failed_count || 0;
    const isPartial = task.status === 'partial_failed';
    return (
      <div>
        <span style={{ fontSize: 12, color: isPartial ? 'var(--amber)' : 'var(--green)' }}>
          {isPartial ? '部分完成' : '完成'}（成功 {task.result_count} 条）
          {(failed > 0 || isPartial) && (
            <span style={{ color: 'var(--red)' }}>
              {failed > 0 ? `，失败 ${failed} 条` : ''}
              {isPartial && task.error_message ? `：${task.error_message}` : ''}
            </span>
          )}
          {elapsed > 0 && `，耗时 ${elapsed}秒`}
        </span>
        {failed > 0 && task.failed_items && task.failed_items.length > 0 && (
          <div style={{ marginTop: 4 }}>
            <Button size="small" type="link" style={{ padding: 0, fontSize: 11 }} onClick={() => setShowFailed(!showFailed)}>
              {showFailed ? '收起失败详情' : '查看失败详情'}
            </Button>
            {showFailed && (
              <div className="fail-list">
                {task.failed_items.map((item, i) => (
                  <div key={i} style={{ marginBottom: 2 }}>
                    {item.file}: {item.reason}
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
      </div>
    );
  }
  if (task.status === 'error') return <span style={{ fontSize: 12, color: 'var(--red)' }}>{task.error_message || '执行失败'}</span>;
  if (task.status === 'interrupted') return <span style={{ fontSize: 12, color: 'var(--red)' }}>程序中断</span>;
  if (task.status === 'cancelled') return <span style={{ fontSize: 12, color: 'var(--amber)' }}>已取消</span>;
  return null;
}

export interface TaskRailProps {
  tasks: BatchTaskInfo[];
  onSelectTask: (taskId: number) => void;
  onDeleteTask: (taskId: number) => void;
  selectedTaskId?: number | null;
  typeLabel: string;
  onRefresh?: () => void;
}

export const TaskRail: React.FC<TaskRailProps> = ({ tasks, onSelectTask, onDeleteTask, selectedTaskId, typeLabel, onRefresh }) => {
  const { message } = App.useApp();

  const handleCancel = async (taskId: number, e: React.MouseEvent) => {
    e.stopPropagation();
    try {
      await taskApi.cancelTask(taskId);
      message.success('任务已取消');
      onRefresh?.();
    } catch (err: any) {
      message.error(err?.response?.data?.error || '取消失败');
    }
  };

  return (
    <div className="task-rail">
      <div className="task-rail-head">
        <span className="hint">任务历史</span>
        {tasks.length > 0 && <span className="hint mono">{tasks.length}</span>}
      </div>
      {tasks.length === 0 && (
        <div className="task-rail-empty">暂无{typeLabel}任务</div>
      )}
      {tasks.map(task => {
        const terminal = isBatchTerminalStatus(task.status);
        const on = task.id === selectedTaskId;
        return (
          <div
            key={task.id}
            className={`task-card${on ? ' on' : ''}${terminal ? '' : ' dead'}`}
            onClick={() => { if (terminal) onSelectTask(task.id); }}
          >
            <div className="task-card-head">
              <TaskStatusBadge status={task.status} />
              <span className="task-card-id">#{task.id}</span>
              <span className="task-card-time">{task.created_at?.slice(0, 19)}</span>
            </div>
            <TaskProgress task={task} />
            {(task.status === 'queued' || terminal) && (
              <div style={{ display: 'flex', gap: 8, marginTop: 6 }}>
                {task.status === 'queued' && (
                  <Button size="small" type="link" style={{ padding: 0, fontSize: 11 }} onClick={(e) => handleCancel(task.id, e)}>
                    取消
                  </Button>
                )}
                {terminal && (
                  <Button
                    size="small" danger type="link" style={{ padding: 0, fontSize: 11 }}
                    onClick={(e) => { e.stopPropagation(); onDeleteTask(task.id); }}
                  >
                    删除记录
                  </Button>
                )}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
};
