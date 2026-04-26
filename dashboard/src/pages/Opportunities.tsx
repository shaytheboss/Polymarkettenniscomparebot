import { useState } from 'react'
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query'
import { api, type Opportunity } from '../api/client'
import { format } from 'date-fns'

const CAT_COLORS: Record<string, string> = {
  STRONG: 'bg-red-900/40 border-red-700 text-red-400',
  MODERATE: 'bg-yellow-900/40 border-yellow-700 text-yellow-400',
  WEAK: 'bg-green-900/40 border-green-700 text-green-400',
}

export default function Opportunities() {
  const qc = useQueryClient()
  const [category, setCategory] = useState('')
  const [tour, setTour] = useState('')
  const [minEdge, setMinEdge] = useState(0)
  const [showResolved, setShowResolved] = useState(false)

  const { data: opps, isLoading } = useQuery({
    queryKey: ['opps', category, tour, minEdge, showResolved],
    queryFn: () =>
      api.opportunities.list({
        category: category || undefined,
        tour: tour || undefined,
        min_edge: minEdge || undefined,
        resolved: showResolved ? undefined : false,
        limit: 100,
      }),
  })

  const resolve = useMutation({
    mutationFn: ({ id, outcome }: { id: number; outcome: string }) =>
      api.opportunities.resolve(id, outcome),
    onSuccess: () => qc.invalidateQueries({ queryKey: ['opps'] }),
  })

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-3 items-center">
        <h1 className="text-xl font-bold text-white mr-4">Opportunities</h1>

        <select
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200"
          value={category}
          onChange={(e) => setCategory(e.target.value)}
        >
          <option value="">All Categories</option>
          <option value="STRONG">Strong (&ge;15pp)</option>
          <option value="MODERATE">Moderate (8-15pp)</option>
          <option value="WEAK">Weak (5-8pp)</option>
        </select>

        <select
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200"
          value={tour}
          onChange={(e) => setTour(e.target.value)}
        >
          <option value="">All Tours</option>
          <option value="ATP">ATP</option>
          <option value="WTA">WTA</option>
        </select>

        <div className="flex items-center gap-2">
          <label className="text-gray-400 text-sm">Min edge (pp):</label>
          <input
            type="number"
            className="bg-gray-800 border border-gray-700 rounded px-2 py-1.5 text-sm w-16 text-gray-200"
            value={minEdge}
            min={0}
            max={50}
            onChange={(e) => setMinEdge(Number(e.target.value))}
          />
        </div>

        <label className="flex items-center gap-2 text-sm text-gray-400">
          <input
            type="checkbox"
            checked={showResolved}
            onChange={(e) => setShowResolved(e.target.checked)}
          />
          Show resolved
        </label>
      </div>

      {isLoading && <div className="text-gray-500">Loading...</div>}

      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="text-gray-400 border-b border-gray-800 text-left">
              <th className="py-2 pr-4">Time</th>
              <th className="py-2 pr-4">Player</th>
              <th className="py-2 pr-4">Score</th>
              <th className="py-2 pr-4">TABLE</th>
              <th className="py-2 pr-4">MARKOV</th>
              <th className="py-2 pr-4">Consensus</th>
              <th className="py-2 pr-4">Polymarket</th>
              <th className="py-2 pr-4">Edge</th>
              <th className="py-2 pr-4">Band</th>
              <th className="py-2 pr-4">Model Gap</th>
              <th className="py-2">Outcome</th>
            </tr>
          </thead>
          <tbody>
            {opps?.map((opp) => (
              <tr key={opp.id} className="border-b border-gray-800/50 hover:bg-gray-900/50">
                <td className="py-2 pr-4 text-gray-500 text-xs whitespace-nowrap">
                  {format(new Date(opp.detected_at), 'HH:mm')}
                </td>
                <td className="py-2 pr-4 text-white font-medium">{opp.back_player_name}</td>
                <td className="py-2 pr-4 text-gray-400 text-xs">{opp.score_text}</td>
                <td className="py-2 pr-4 text-blue-400">{(opp.table_prob * 100).toFixed(1)}%</td>
                <td className="py-2 pr-4 text-purple-400">{(opp.markov_prob * 100).toFixed(1)}%</td>
                <td className="py-2 pr-4 text-white">{(opp.consensus_prob * 100).toFixed(1)}%</td>
                <td className="py-2 pr-4 text-yellow-400">{(opp.poly_price * 100).toFixed(1)}%</td>
                <td className="py-2 pr-4">
                  <span
                    className={`px-2 py-0.5 rounded border text-xs font-bold ${
                      CAT_COLORS[opp.edge_category] ?? ''
                    }`}
                  >
                    +{opp.edge_pp.toFixed(1)}pp
                  </span>
                </td>
                <td className="py-2 pr-4 text-gray-400 text-xs">{opp.extra?.elo_band}</td>
                <td className="py-2 pr-4 text-gray-400 text-xs">
                  {opp.model_agreement.toFixed(1)}pp
                </td>
                <td className="py-2">
                  {opp.resolved ? (
                    <span
                      className={
                        opp.outcome === 'WIN'
                          ? 'text-green-400'
                          : opp.outcome === 'LOSS'
                          ? 'text-red-400'
                          : 'text-gray-400'
                      }
                    >
                      {opp.outcome}
                      {opp.pnl_units != null && (
                        <span className="text-xs ml-1">
                          ({opp.pnl_units > 0 ? '+' : ''}{opp.pnl_units.toFixed(2)}u)
                        </span>
                      )}
                    </span>
                  ) : (
                    <div className="flex gap-1">
                      <button
                        onClick={() => resolve.mutate({ id: opp.id, outcome: 'WIN' })}
                        className="text-xs px-2 py-0.5 bg-green-900/50 hover:bg-green-800/50 text-green-400 rounded border border-green-800"
                      >
                        W
                      </button>
                      <button
                        onClick={() => resolve.mutate({ id: opp.id, outcome: 'LOSS' })}
                        className="text-xs px-2 py-0.5 bg-red-900/50 hover:bg-red-800/50 text-red-400 rounded border border-red-800"
                      >
                        L
                      </button>
                    </div>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
        {!opps?.length && !isLoading && (
          <div className="text-gray-500 text-sm py-6 text-center">No opportunities match the filters.</div>
        )}
      </div>
    </div>
  )
}
