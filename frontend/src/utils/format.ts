/**
 * 格式化秒数为人类可读的时间字符串
 * @param seconds 秒数
 * @returns 例如 "45秒"、"2分30秒"、"3分钟"
 */
export function fmtEta(seconds: number): string {
  if (seconds <= 0) return '';
  if (seconds < 60) return `${Math.round(seconds)}秒`;
  const m = Math.floor(seconds / 60);
  const sec = Math.round(seconds % 60);
  return sec > 0 ? `${m}分${sec}秒` : `${m}分钟`;
}
