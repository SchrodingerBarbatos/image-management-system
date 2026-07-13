import React, { useState } from 'react';
import { Tag, Space, Button, Progress, Typography, message } from 'antd';
import { BatchTaskInfo, taskApi } from '../services/api';
import { fmtEta } from '../utils/format';
import { BATCH_STATUS_LABELS, isBatchTerminalStatus } from '../utils/taskStatus';

const { Text } = Typography;

export function TaskStatusBadge({ status }: { status: string }) {
  const cfg = BATCH_STATUS_LABELS[status] || { color: 'default', label: status };
  return <Tag color={cfg.color}>{cfg.label}</Tag>;
}

export function TaskProgress({ task }: { task: BatchTaskInfo }) {
  const [showFailed, setShowFailed] = useState(false);

  if (task.status === 'queued') return <Text type="secondary">排队中…</Text>;
  if (task.status === 'running') {
    const percent = task.percent || (task.total > 0 ? Math.round((task.progress / task.total) * 100) : 0);
    return (
      <div>
        <Progress percent={percent} size="small" />
        <div style={{ fontSize: 12, color: '#888', marginTop: 4 }}>
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
        <Text type={isPartial ? 'warning' : 'success'}>
          {isPartial ? '部分完成' : '完成'}（成功 {task.result_count} 条）
          {(failed > 0 || isPartial) && (
            <Text type="danger">
              {failed > 0 ? `，失败 ${failed} 条` : ''}
              {isPartial && task.error_message ? `：${task.error_message}` : ''}
            </Text>
          )}
          {elapsed > 0 && `，耗时 ${elapsed}秒`}
        </Text>
        {failed > 0 && task.failed_items && task.failed_items.length > 0 && (
          <div style={{ marginTop: 4 }}>
            <Button size="small" type="link" style={{ padding: 0 }} onClick={() => setShowFailed(!showFailed)}>
              {showFailed ? '收起失败详情' : '查看失败详情'}
            </Button>
            {showFailed && (
              <div style={{ fontSize: 12, color: '#ff4d4f', marginTop: 4, maxHeight: 120, overflow: 'auto' }}>
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
  if (task.status === 'error') return <Text type="danger">{task.error_message || '执行失败'}</Text>;
  if (task.status === 'interrupted') return <Text type="danger">程序中断</Text>;
  if (task.status === 'cancelled') return <Text type="warning">已取消</Text>;
  return null;
}

export interface TaskListProps {
  tasks: BatchTaskInfo[];
  onSelectTask: (taskId: number) => void;
  onDeleteTask: (taskId: number) => void;
  selectedTaskId?: number | null;
  typeLabel: string;
  onRefresh?: () => void;
}

export const TaskList: React.FC<TaskListProps> = ({ tasks, onSelectTask, onDeleteTask, selectedTaskId, typeLabel, onRefresh }) => {
  if (tasks.length === 0) {
    return <Text type="secondary">暂无{typeLabel}任务</Text>;
  }

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
    <div style={{ maxHeight: 300, overflowY: 'auto' }}>
      {tasks.map(task => (
        <div
          key={task.id}
          style={{
            padding: '8px 12px',
            marginBottom: 8,
            border: '1px solid #f0f0f0',
            borderRadius: 4,
            background: task.id === selectedTaskId ? '#e6f7ff' : undefined,
            cursor: isBatchTerminalStatus(task.status) ? 'pointer' : 'default',
          }}
          onClick={() => {
            if (isBatchTerminalStatus(task.status)) {
              onSelectTask(task.id);
            }
          }}
        >
          <Space direction="vertical" size={4} style={{ width: '100%' }}>
            <Space>
              <TaskStatusBadge status={task.status} />
              <Text type="secondary" style={{ fontSize: 12 }}>#{task.id}</Text>
              <Text type="secondary" style={{ fontSize: 12 }}>{task.created_at?.slice(0, 19)}</Text>
            </Space>
            <TaskProgress task={task} />
            <Space>
              {['queued'].includes(task.status) && (
                <Button size="small" type="link" onClick={(e) => handleCancel(task.id, e)}>
                  取消
                </Button>
              )}
              {isBatchTerminalStatus(task.status) && (
                <Button size="small" danger type="link" onClick={(e) => { e.stopPropagation(); onDeleteTask(task.id); }}>
                  删除记录
                </Button>
              )}
            </Space>
          </Space>
        </div>
      ))}
    </div>
  );
};
