import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../api/client'

export default function Players() {
  const [tour, setTour] = useState('ATP')
  const [search, setSearch] = useState('')

  const { data: players, isLoading } = useQuery({
    queryKey: ['players', tour, search],
    queryFn: () => api.players.list({ tour: tour || undefined, search: search || undefined }),
  })

  return (
    <div className="space-y-4">
      <div className="flex gap-3 items-center flex-wrap">
        <h1 className="text-xl font-bold text-white mr-4">Players / ELO</h1>
        <select
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200"
          value={tour}
          onChange={(e) => setTour(e.target.value)}
        >
          <option value="">All</option>
          <option value="ATP">ATP</option>
          <option value="WTA">WTA</option>
        </select>
        <input
          className="bg-gray-800 border border-gray-700 rounded px-3 py-1.5 text-sm text-gray-200 w-48"
          placeholder="Search player..."
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
      </div>

      {isLoading && <div className="text-gray-500">Loading...</div>}

      <table className="w-full text-sm">
        <thead>
          <tr className="text-gray-400 border-b border-gray-800 text-left">
            <th className="py-2 pr-4">#</th>
            <th className="py-2 pr-4">Name</th>
            <th className="py-2 pr-4">ELO</th>
            <th className="py-2 pr-4">Hard</th>
            <th className="py-2 pr-4">Clay</th>
            <th className="py-2 pr-4">Grass</th>
            <th className="py-2">Rank</th>
          </tr>
        </thead>
        <tbody>
          {players?.map((p, i) => (
            <tr key={p.id} className="border-b border-gray-800/50 hover:bg-gray-900/50">
              <td className="py-1.5 pr-4 text-gray-600">{i + 1}</td>
              <td className="py-1.5 pr-4 text-white">{p.name}</td>
              <td className="py-1.5 pr-4 font-bold text-green-400">{p.current_elo?.toFixed(0) ?? '—'}</td>
              <td className="py-1.5 pr-4 text-blue-400">{p.elo_hard?.toFixed(0) ?? '—'}</td>
              <td className="py-1.5 pr-4 text-orange-400">{p.elo_clay?.toFixed(0) ?? '—'}</td>
              <td className="py-1.5 pr-4 text-emerald-400">{p.elo_grass?.toFixed(0) ?? '—'}</td>
              <td className="py-1.5 text-gray-400">{p.ranking ?? '—'}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}
