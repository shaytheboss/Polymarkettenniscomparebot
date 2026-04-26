const BASE = '/api'

async function get<T>(path: string, params?: Record<string, string | number | boolean>): Promise<T> {
  const url = new URL(BASE + path, window.location.origin)
  if (params) {
    Object.entries(params).forEach(([k, v]) => {
      if (v !== undefined && v !== null) url.searchParams.set(k, String(v))
    })
  }
  const res = await fetch(url.toString())
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function patch<T>(path: string, body?: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: body ? JSON.stringify(body) : undefined,
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

async function put<T>(path: string, body: unknown): Promise<T> {
  const res = await fetch(BASE + path, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`)
  return res.json()
}

export type Match = {
  id: number
  external_id: string
  player1: string
  player2: string
  player1_elo: number
  player2_elo: number
  tour: string
  surface: string
  tournament: string
  status: string
  score: {
    p1_sets: number; p2_sets: number
    p1_games: number; p2_games: number
    p1_pts: number; p2_pts: number
    server: number; in_tiebreak: boolean
    text: string
  }
  poly_price_p1: number | null
  latest_probs?: Snapshot
  updated_at: string
}

export type Snapshot = {
  table_prob_p1: number
  markov_prob_p1: number
  consensus_prob_p1: number
  model_agreement: number
  poly_price_p1: number | null
  edge_consensus: number | null
  elo_band: string
  notes: string
}

export type Opportunity = {
  id: number
  match_id: number
  detected_at: string
  back_player: number
  back_player_name: string
  table_prob: number
  markov_prob: number
  consensus_prob: number
  poly_price: number
  edge_pp: number
  model_agreement: number
  edge_category: string
  score_text: string
  score: { p1_sets: number; p2_sets: number; p1_games: number; p2_games: number }
  resolved: boolean
  outcome: string | null
  pnl_units: number | null
  extra: Record<string, string>
}

export type Stats = {
  total: number
  resolved: number
  wins: number
  losses: number
  win_rate: number
  avg_edge_pp: number
}

export type Player = {
  id: number
  name: string
  tour: string
  current_elo: number | null
  elo_hard: number | null
  elo_clay: number | null
  elo_grass: number | null
  ranking: number | null
}

export const api = {
  matches: {
    list: (params?: { status?: string; tour?: string }) =>
      get<Match[]>('/matches', params as any),
    get: (id: number) => get<Match>(`/matches/${id}`),
    snapshots: (id: number) => get<Snapshot[]>(`/matches/${id}/snapshots`),
  },
  opportunities: {
    list: (params?: {
      category?: string; tour?: string; min_edge?: number
      date_from?: string; resolved?: boolean; limit?: number
    }) => get<Opportunity[]>('/opportunities', params as any),
    stats: () => get<Stats>('/opportunities/stats'),
    resolve: (id: number, outcome: string) =>
      patch<Opportunity>(`/opportunities/${id}/resolve?outcome=${outcome}`),
  },
  players: {
    list: (params?: { tour?: string; search?: string }) =>
      get<Player[]>('/players', params as any),
  },
  settings: {
    get: () => get<Record<string, string>>('/settings'),
    set: (key: string, value: string) => put(`/settings/${key}`, { value }),
  },
}
