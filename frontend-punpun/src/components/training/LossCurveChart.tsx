import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend } from "recharts";

/** One point on the loss chart. Real Engine data can have gaps (e.g. eval
 *  loss is only recorded every N steps), so both series are nullable —
 *  recharts simply skips a null point instead of drawing a spurious dip. */
export interface LossChartPoint {
  step: number;
  trainLoss: number | null;
  valLoss: number | null;
}

export function LossCurveChart({ data }: { data: LossChartPoint[] }) {
  return (
    <div className="h-[280px]">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data}>
          <defs>
            <linearGradient id="trainGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="hsl(243,75%,59%)" stopOpacity={0.2} />
              <stop offset="95%" stopColor="hsl(243,75%,59%)" stopOpacity={0} />
            </linearGradient>
            <linearGradient id="valGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="hsl(38,92%,50%)" stopOpacity={0.2} />
              <stop offset="95%" stopColor="hsl(38,92%,50%)" stopOpacity={0} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" className="stroke-border" />
          <XAxis
            dataKey="step"
            tick={{ fill: "hsl(220,9%,46%)", fontSize: 11 }}
            label={{ value: "Training Steps", position: "insideBottom", offset: -5, fill: "hsl(220,9%,46%)", fontSize: 11 }}
          />
          <YAxis
            tick={{ fill: "hsl(220,9%,46%)", fontSize: 11 }}
            label={{ value: "Loss", angle: -90, position: "insideLeft", fill: "hsl(220,9%,46%)", fontSize: 11 }}
          />
          <Tooltip
            contentStyle={{
              backgroundColor: "hsl(0,0%,100%)",
              border: "1px solid hsl(220,13%,91%)",
              borderRadius: "8px",
              fontSize: 12,
            }}
            formatter={(value: number | null) => [value === null ? "—" : value.toFixed(4), ""]}
          />
          <Legend verticalAlign="top" height={36} iconType="line" />
          <Area
            type="monotone"
            dataKey="trainLoss"
            name="Train Loss"
            stroke="hsl(243,75%,59%)"
            fill="url(#trainGrad)"
            strokeWidth={2}
            connectNulls
            dot={false}
          />
          <Area
            type="monotone"
            dataKey="valLoss"
            name="Val Loss"
            stroke="hsl(38,92%,50%)"
            fill="url(#valGrad)"
            strokeWidth={2}
            strokeDasharray="4 4"
            connectNulls
            dot={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}
