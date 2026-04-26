import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api, type Match } from '../api/client'

function ProbBar({ table, markov, poly }: { table: number; markov: number; poly?: number | null }) {
  return (
    <div className="flex gap-3 text-xs">
      <span className="text-blue-400">T:{(table * 100).toFixed(1)}%</span>
      <span className="text-purple-400">M:{(markov * 100).toFixed(1)}%</span>
      {poly != null && (
        <span className={`font-bold ${Math.abs(((table + markov) / 2) - poly) > 0.05 ? 'text-yellow-400' : 'text-gray-400'}`}>
          P:{(poly * 100).toFixed(1)}%
        </span>
      )}
    </div>
  )
}

export default function Matches() {
  const [status, setStatus] = useState('live')
  const [tour, setTour] = useState('')

  const { data: matches, isLoading } = useQuery({
    queryKey: ['matches', status, tour],
    queryFn: () => api.matches.list({ status: status || undefined, tour: tour || undefined }),
    refetchInterval: 15_000,
  })

  return (
    <div className="space-y-4">
      <div className="flex gap-3 items-center flex-wrap">
        <h1 className="text-xl font-bold text-white mr-4">Matches</h1>
        <select
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200"
          value={status}
          onChange={(e) => setStatus(e.target.value)}
        >
          <option value="">All</option>
          <option value="live">Live</option>
          <option value="scheduled">Scheduled</option>
          <option value="finished">Finished</option>
        </select>
        <select
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200"
          value={tour}
          onChange={(e) => setTour(e.target.value)}
        >
          <option value="">ATP + WTA</option>
          <option value="ATP">ATP</option>
          <option value="WTA">WTA</option>
        </select>
      </div>

      {isLoading && <div className="text-gray-500">Loading...</div>}

      <div className="space-y-3">
        {matches?.map((m) => (
          <div key={m.id} className="bg-gray-900 border border-gray-800 rounded-lg p-4">
            <div className="flex justify-between items-start">
              <div>
                <div className="font-medium text-white">
                  {m.player1} <span className="text-gray-500">vs</span> {m.player2}
                </div>
                <div className="text-gray-500 text-xs mt-0.5">
                  {m.tournament} · {m.tour} · {m.surface}
                </div>
              </div>
              <div className="text-right">
                <span
                  className={`text-xs px-2 py-0.5 rounded ${
                    m.status === 'live'
                      ? 'bg-green-900/50 text-green-400 border border-green-800'
                      : 'bg-gray-800 text-gray-400'
                  }`}
                >
                  {m.status}
                </span>
              </div>
            </div>

            <div className="mt-2 text-sm text-gray-300">{m.score?.text || '—'}</div>

            {m.latest_probs && (
              <div className="mt-2">
                <ProbBar
                  table={m.latest_probs.table_prob_p1}
                  markov={m.latest_probs.markov_prob_p1}
                  poly={m.latest_probs.poly_price_p1}
                />
                <div className="text-gray-600 text-xs mt-1">
                  Band: {m.latest_probs.elo_band} · {m.latest_probs.notes}
                  {m.latest_probs.edge_consensus != null && (
                    <span className={`ml-2 font-bold ${Math.abs(m.latest_probs.edge_consensus) > 0.05 ? 'text-yellow-400' : ''}`}>
                      edge: {(m.latest_probs.edge_consensus * 100).toFixed(1)}pp
                    </span>
                  )}
                </div>
              </div>
            )}

            <div className="mt-2 text-gray-600 text-xs">
              ELO: {m.player1} {m.player1_elo?.toFixed(0) ?? '?'} vs {m.player2} {m.player2_elo?.toFixed(0) ?? '?'}
              {m.poly_price_p1 != null && (
                <span className="ml-3 text-yellow-600">PM: {(m.poly_price_p1 * 100).toFixed(1)}%</span>
              )}
            </div>
          </div>
        ))}
        {!matches?.length && !isLoading && (
          <div className="text-gray-500 text-sm">No matches found.</div>
        )}
      </div>
    </div>
  )
}
