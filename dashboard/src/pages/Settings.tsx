import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api } from '../api/client'

function SettingRow({
  label, settingKey, description, currentValue, onSave,
}: {
  label: string
  settingKey: string
  description: string
  currentValue: string
  onSave: (key: string, value: string) => void
}) {
  const [val, setVal] = useState(currentValue)
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="flex justify-between items-start gap-4">
        <div>
          <div className="text-white font-medium">{label}</div>
          <div className="text-gray-500 text-xs mt-0.5">{description}</div>
        </div>
        <div className="flex gap-2 items-center flex-shrink-0">
          <input
            className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 w-28"
            value={val}
            onChange={(e) => setVal(e.target.value)}
          />
          <button
            onClick={() => onSave(settingKey, val)}
            className="px-3 py-1.5 bg-green-900/50 hover:bg-green-800 text-green-400 rounded border border-green-800 text-sm"
          >
            Save
          </button>
        </div>
      </div>
    </div>
  )
}

export default function Settings() {
  const qc = useQueryClient()
  const { data: settings } = useQuery({ queryKey: ['settings'], queryFn: api.settings.get })

  const saveSetting = useMutation({
    mutationFn: ({ key, value }: { key: string; value: string }) => api.settings.set(key, value),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['settings'] }),
  })

  const configs = [
    {
      label: 'Min Edge (pp)',
      key: 'default_min_edge_pp',
      desc: 'Minimum edge in percentage points to flag an opportunity',
    },
    {
      label: 'Max Model Gap (pp)',
      key: 'default_max_model_gap_pp',
      desc: 'Opportunities are only shown when TABLE vs MARKOV disagree by less than this',
    },
    {
      label: 'Alert Dedup (minutes)',
      key: 'alert_dedup_minutes',
      desc: 'Minimum minutes between repeated alerts for the same match direction',
    },
    {
      label: 'Live Score Interval (s)',
      key: 'live_scores_interval',
      desc: 'How often to poll live scores (seconds). Min 10.',
    },
    {
      label: 'Polymarket Interval (s)',
      key: 'polymarket_interval',
      desc: 'How often to refresh Polymarket prices (seconds)',
    },
  ]

  return (
    <div className="space-y-4 max-w-2xl">
      <h1 className="text-xl font-bold text-white">Bot Settings</h1>
      <p className="text-gray-400 text-sm">
        Changes take effect on next job cycle. Restart not required.
      </p>

      {configs.map((c) => (
        <SettingRow
          key={c.key}
          label={c.label}
          settingKey={c.key}
          description={c.desc}
          currentValue={settings?.[c.key] ?? ''}
          onSave={(key, value) => saveSetting.mutate({ key, value })}
        />
      ))}

      <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
        <h2 className="text-white font-medium mb-2">ELO Model Documentation</h2>
        <div className="text-gray-500 text-xs space-y-1">
          <div>• <span className="text-blue-400">TABLE model</span>: Uses empirical reference tables (O'Shannessy, Sackmann, TennisRatio) with Markov chain state lookups</div>
          <div>• <span className="text-purple-400">MARKOV model</span>: Full point-by-point recursive DP from current match state using ELO-derived serve probabilities</div>
          <div>• <span className="text-white">Consensus</span>: 45% TABLE + 55% MARKOV (Markov weighted slightly higher for precision)</div>
          <div>• Edge is only flagged when both models agree within the configured model gap threshold</div>
          <div>• ELO Bands: E0 &lt;50, E1 50-150, E2 150-300, E3 300+</div>
          <div>• Surface adjustments applied: clay -2.5pp fav, grass +1.5pp fav (hard = reference)</div>
        </div>
      </div>
    </div>
  )
}
