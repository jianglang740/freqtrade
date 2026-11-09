/*
 * 净值曲线。纯 SVG，无图表库：网格 + 面积填充 + 折线，风格跟全站一致。
 * data 为空时渲染占位框，避免为没有回测口径的策略编造曲线。
 */

type Props = {
  data: number[] | null;
  label?: string;
};

const W = 200;
const H = 110;

export function EquityCurve({ data, label }: Props) {
  if (!data || data.length < 2) {
    return (
      <div className="chart-frame">
        <div
          style={{
            height: '100%',
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            flexDirection: 'column',
            gap: 8,
            color: 'var(--rh-lav-dim)',
            fontSize: 11,
            letterSpacing: '0.14em',
            textAlign: 'center',
            padding: 12,
            lineHeight: 1.8,
          }}
        >
          <span style={{ fontSize: 18, color: 'var(--rh-line)' }}>▚▞</span>
          {label || '暂无曲线'}
        </div>
      </div>
    );
  }

  const min = Math.min(...data, 1);
  const max = Math.max(...data, 1);
  const pad = (max - min) * 0.08 || 0.02;
  const lo = min - pad;
  const hi = max + pad;

  const x = (i: number) => (i / (data.length - 1)) * W;
  const y = (v: number) => H - ((v - lo) / (hi - lo)) * H;

  const line = data.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(v).toFixed(2)}`).join(' ');
  const area = `${line} L${W},${H} L0,${H} Z`;
  const zeroY = y(1);

  return (
    <div className="chart-frame">
      <svg viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none" aria-hidden="true">
        <defs>
          <linearGradient id="eqFill" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#f9c74f" stopOpacity="0.34" />
            <stop offset="100%" stopColor="#f9c74f" stopOpacity="0.02" />
          </linearGradient>
        </defs>

        {[0.25, 0.5, 0.75].map((p) => (
          <line
            key={p}
            x1={0}
            x2={W}
            y1={H * p}
            y2={H * p}
            stroke="#33205c"
            strokeWidth={0.6}
            strokeDasharray="2 3"
            vectorEffect="non-scaling-stroke"
          />
        ))}

        {zeroY > 0 && zeroY < H && (
          <line
            x1={0}
            x2={W}
            y1={zeroY}
            y2={zeroY}
            stroke="#4b2f7e"
            strokeWidth={0.8}
            strokeDasharray="4 3"
            vectorEffect="non-scaling-stroke"
          />
        )}

        <path d={area} fill="url(#eqFill)" />
        <path d={line} fill="none" stroke="#f9c74f" strokeWidth={1.6} vectorEffect="non-scaling-stroke" />
        <circle cx={x(data.length - 1)} cy={y(data[data.length - 1])} r={2.2} fill="#f4e6c8" />
      </svg>
    </div>
  );
}
