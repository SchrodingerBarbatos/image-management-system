import React from 'react';

/* ---------- LED 状态点 ---------- */
export const Led: React.FC<{ color?: 'amber' | 'blue' | 'green' | 'red' }> = ({ color }) => (
  <span className={`led${color ? ` ${color}` : ''}`} />
);

/* ---------- 光条进度（transform scaleX，GPU 合成） ---------- */
interface LightBarProps {
  /** 0~1；不传为不定进度（仅光束横扫） */
  value?: number;
  state?: 'active' | 'done' | 'error';
}

export const LightBar: React.FC<LightBarProps> = ({ value, state = 'active' }) => {
  const p = Math.max(0, Math.min(1, value ?? 0));
  const cls = state === 'done' ? ' done' : state === 'error' ? ' error' : '';
  return (
    <div className="lbar">
      {value !== undefined && (
        <div className={`lbar-fill${cls}`} style={{ transform: `scaleX(${p})` }} />
      )}
      {state === 'active' && <div className="lbar-beam" />}
    </div>
  );
};

/* ---------- 分段选择器 ---------- */
interface SegProps<T extends string> {
  value: T;
  onChange: (v: T) => void;
  options: { value: T; label: React.ReactNode }[];
}

export function Seg<T extends string>({ value, onChange, options }: SegProps<T>) {
  return (
    <div className="seg" role="tablist">
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          aria-selected={value === o.value}
          className={`seg-btn${value === o.value ? ' on' : ''}`}
          onClick={() => onChange(o.value)}
        >
          {o.label}
        </button>
      ))}
    </div>
  );
}

/* ---------- 统计卡 ---------- */
export const StatCard: React.FC<{ num: React.ReactNode; label: string; accent?: string }> = ({
  num,
  label,
  accent,
}) => (
  <div className="stat-card">
    <div className="stat-num" style={accent ? { color: accent } : undefined}>
      {num}
    </div>
    <div className="stat-label">{label}</div>
  </div>
);

/* ---------- 空状态 ---------- */
export const EmptyBlock: React.FC<{ icon?: React.ReactNode; text: string; ok?: boolean }> = ({
  icon,
  text,
  ok,
}) => (
  <div className="empty-block">
    {icon && <span className={ok ? 'ok' : undefined}>{icon}</span>}
    <span>{text}</span>
  </div>
);

/* ---------- 后台任务浮卡 ---------- */
export const ActivityCard: React.FC<{
  title: string;
  ledColor?: 'amber' | 'blue' | 'green' | 'red';
  value?: number;
  meta?: React.ReactNode;
}> = ({ title, ledColor = 'blue', value, meta }) => (
  <div className="activity-card">
    <div className="activity-title">
      <Led color={ledColor} />
      {title}
    </div>
    <LightBar value={value} />
    {meta && <div className="activity-meta">{meta}</div>}
  </div>
);
