import {
  CartesianGrid,
  Legend,
  Line,
  LineChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'

import type { LossPoint } from '@/hooks/useJobProgress'

const AXIS_STYLE = { fontSize: 11, fontFamily: '"Fira Code", monospace', fill: '#94A3B8' }

export function LossCurveChart({ data }: { data: LossPoint[] }) {
  if (data.length === 0) {
    return (
      <div className="flex h-56 items-center justify-center rounded-md border border-dashed border-line/60 text-xs text-body-muted">
        Loss curve appears once training steps report
      </div>
    )
  }

  return (
    <div role="img" aria-label="Training and evaluation loss by step" className="h-56">
      <ResponsiveContainer width="100%" height="100%">
        <LineChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -16 }}>
          <CartesianGrid stroke="#475569" strokeOpacity={0.25} vertical={false} />
          <XAxis dataKey="step" tick={AXIS_STYLE} tickLine={false} axisLine={{ stroke: '#475569' }} />
          <YAxis tick={AXIS_STYLE} tickLine={false} axisLine={false} domain={['auto', 'auto']} />
          <Tooltip
            contentStyle={{
              backgroundColor: '#1E293B',
              border: '1px solid #475569',
              borderRadius: 8,
              fontSize: 12,
              fontFamily: '"Fira Code", monospace',
            }}
            labelFormatter={(step) => `step ${step}`}
          />
          <Legend wrapperStyle={{ fontSize: 12 }} />
          <Line
            type="monotone"
            dataKey="train_loss"
            name="train loss"
            stroke="#22C55E"
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
          <Line
            type="monotone"
            dataKey="eval_loss"
            name="eval loss"
            stroke="#38BDF8"
            strokeWidth={2}
            dot={false}
            connectNulls
            isAnimationActive={false}
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
