import React from 'react';
import { Tag, Space, Button, Progress, Spin, Typography } from 'antd';
import { BatchTaskInfo } from '../services/api';

const { Text } = Typography;

const STATUS_CONFIG: Record<string, { color: string; label: string }> = {
  queued: { color: 'default', label: '排队中' },
  running: { color: 'processing', label: '运行中' },
  done: { color: 'success', label: '已完成' },
  error: { color: 'error', label: '失败' },
  cancelled: { color: 'warning', label: '已取消' },
  interrupted: { color: 'error', label: '中断' },
};

export function TaskStatusBadge({ status }: { status: string }) {
  const cfg = STATUS_CONFIG[status] || { color: 'default', label: status };
  return <Tag color={cfg.color}>{cfg.label}</Tag>;
}

export function TaskProgress({ task }: { task: BatchTaskInfo }) {
  if (task.status === 'queued') return <Text type="secondary">排队中…</Text>;
  if (task.status === 'running') {
    const percent = task.total > 0 ? Math.round((task.progress / task.total) * 100) : 0;
    return <Progress percent={percent} size="small" />;
  }
  if (task.status === 'done') return <Text type="success">完成（{task.result_count} 条结果）</Text>;
  if (task.status === 'error') return <Text type="danger">{task.error_message || '执行失败'}</Text>;
  if (task.status === 'interrupted') return <Text type="danger">程序中断</Text>;
  return null;
}

export interface TaskListProps {
  tasks: BatchTaskInfo[];
  onSelectTask: (taskId: number) => void;
  onDeleteTask: (taskId: number) => void;
  selectedTaskId?: number | null;
  typeLabel: string;
}

export const TaskList: React.FC<TaskListProps> = ({ tasks, onSelectTask, onDeleteTask, selectedTaskId, typeLabel }) => {
  if (tasks.length === 0) {
    return <Text type="secondary">暂无{typeLabel}任务</Text>;
  }

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
            cursor: ['done', 'error', 'interrupted'].includes(task.status) ? 'pointer' : 'default',
          }}
          onClick={() => {
            if (['done', 'error', 'interrupted'].includes(task.status)) {
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
            {['done', 'error', 'interrupted', 'cancelled'].includes(task.status) && (
              <Button size="small" danger type="link" onClick={(e) => { e.stopPropagation(); onDeleteTask(task.id); }}>
                删除记录
              </Button>
            )}
          </Space>
        </div>
      ))}
    </div>
  );
};
