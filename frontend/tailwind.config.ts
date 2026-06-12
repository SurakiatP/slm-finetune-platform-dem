import type { Config } from 'tailwindcss'

export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        bg: '#0F172A',
        surface: { DEFAULT: '#1E293B', 2: '#27364B' },
        accent: {
          DEFAULT: '#22C55E',
          hover: '#16A34A',
          muted: 'rgba(34,197,94,.15)',
        },
        body: { DEFAULT: '#F8FAFC', muted: '#94A3B8' },
        line: '#475569',
        danger: { DEFAULT: '#EF4444', muted: 'rgba(239,68,68,.15)' },
        warn: { DEFAULT: '#F59E0B', muted: 'rgba(245,158,11,.15)' },
        info: { DEFAULT: '#38BDF8', muted: 'rgba(56,189,248,.15)' },
        violet: { DEFAULT: '#A78BFA', muted: 'rgba(167,139,250,.15)' },
      },
      fontFamily: {
        sans: ['"Fira Sans"', 'system-ui', 'sans-serif'],
        mono: ['"Fira Code"', 'ui-monospace', 'monospace'],
      },
    },
  },
  plugins: [],
} satisfies Config
