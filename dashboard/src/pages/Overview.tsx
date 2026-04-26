import { useQuery } from '@tanstack/react-query'
import { api, type Opportunity, type Stats } from '../api/client'
import { format } from 'date-fns'

function StatCard({ label, value, sub }: { label: string; value: string | number; sub?: string }) {
  return (
    <div className="bg-gray-900 border border-gray-800 rounded-lg p-4">
      <div className="text-gray-400 text-xs uppercase tracking-wider">{label}</div>
      <div className="text-2xl font-bold text-white mt-1">{value}</div>
      {sub && <div className="text-gray-500 text-xs mt-1">{sub}</div>}
    </div>
  )
}

function categoryColor(cat: string) {
  if (cat === 'STRONG') return 'text-red-400'
  if (cat === 'MODERATE') return 'text-yellow-400'
  return 'text-green-400'
}

function EdgeBadge({ category, edge }: { category: string; edge: number }) {
  return (
    <span className={`font-bold ${categoryColor(category)}`}>
      {category} +{edge.toFixed(1)}pp
    </span>
  )
}

export default function Overview() {
  const { data: stats } = useQuery({ queryKey: ['stats'], queryFn: api.opportunities.stats })
  const { data: recentOpps } = useQuery({
    queryKey: ['opps-recent'],
    queryFn: () => api.opportunities.list({ limit: 10 }),
  })
  const { data: liveMatches } = useQuery({
    queryKey: ['live-matches'],
    queryFn: () => api.matches.list({ status: 'live' }),
  })

  return (
    <div className="space-y-6">
      <h1 className="text-xl font-bold text-white">Dashboard Overview</h1>

      <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
        <StatCard label="Live Matches" value={liveMatches?.length ?? 0} />
        <StatCard label="Total Opportunities" value={stats?.total ?? 0} />
        <StatCard
          label="Win Rate"
          value={stats ? `${(stats.win_rate * 100).toFixed(0)}%` : '—'}
          sub={`${stats?.wins ?? 0}W / ${stats?.losses ?? 0}L`}
        />
        <StatCard
          label="Avg Edge"
          value={stats ? `${stats.avg_edge_pp.toFixed(1)}pp` : '—'}
        />
      </div>

      <div className="grid md:grid-cols-2 gap-6">
        <section>
          <h2 className="text-sm uppercase tracking-wider text-gray-400 mb-3">Recent Opportunities</h2>
          <div className="space-y-2">
            {recentOpps?.map((opp) => (
              <div key={opp.id} className="bg-gray-900 border border-gray-800 rounded p-3 text-sm">
                <div className="flex justify-between items-start">
                  <span className="text-white font-medium">{opp.back_player_name}</span>
                  <EdgeBadge category={opp.edge_category} edge={opp.edge_pp} />
                </div>
                <div className="text-gray-500 text-xs mt-1">
                  {opp.score_text} · {opp.extra?.tournament ?? ''} · {opp.extra?.surface ?? ''}
                </div>
                <div className="text-gray-400 text-xs mt-1">
                  Cons {(opp.consensus_prob * 100).toFixed(1)}% vs Poly {(opp.poly_price * 100).toFixed(1)}%
                  · Model gap {opp.model_agreement.toFixed(1)}pp
                </div>
              </div>
            ))}
            {!recentOpps?.length && (
              <div className="text-gray-500 text-sm">No opportunities yet today.</div>
            )}
          </div>
        </section>

        <section>
          <h2 className="text-sm uppercase tracking-wider text-gray-400 mb-3">Live Matches</h2>
          <div className="space-y-2">
            {liveMatches?.map((m) => (
              <div key={m.id} className="bg-gray-900 border border-gray-800 rounded p-3 text-sm">
                <div className="flex justify-between">
                  <span className="text-white">{m.player1} vs {m.player2}</span>
                  <span className="text-gray-500 text-xs">{m.tour} · {m.surface}</span>
                </div>
                <div className="text-gray-400 text-xs mt-1">{m.score?.text ?? 'Starting'}</div>
                {m.latest_probs && (
                  <div className="text-xs mt-1 flex gap-3">
                    <span className="text-blue-400">
                      TABLE {(m.latest_probs.table_prob_p1 * 100).toFixed(1)}%
                    </span>
                    <span className="text-purple-400">
                      MKV {(m.latest_probs.markov_prob_p1 * 100).toFixed(1)}%
                    </span>
                    {m.latest_probs.poly_price_p1 != null && (
                      <span className="text-yellow-400">
                        POLY {(m.latest_probs.poly_price_p1 * 100).toFixed(1)}%
                      </span>
                    )}
                  </div>
                )}
              </div>
            ))}
            {!liveMatches?.length && (
              <div className="text-gray-500 text-sm">No live matches right now.</div>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}
