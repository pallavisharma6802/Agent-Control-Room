"use client"

import { useState } from "react"
import useSWR from "swr"
import { Activity, AlertCircle, CheckCircle2, Database, TrendingUp } from "lucide-react"
import { BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Cell } from "recharts"

const fetcher = (url: string) => fetch(url).then((res) => res.json())

interface Stats {
  total_traces: number
  hallucinated_count: number
  hallucination_rate: number
  unique_sessions: number
  recent_traces: Array<{
    timestamp: string
    is_hallucinated: boolean
    session_id: string
  }>
}

interface EvalResults {
  accuracy: number
  total: number
  correct: number
  results: Array<{
    category: string
    correct: boolean
  }>
}

export default function Dashboard() {
  const [prompt, setPrompt] = useState("")
  const [loading, setLoading] = useState(false)
  const [result, setResult] = useState<any>(null)

  const { data: stats, error: statsError } = useSWR<Stats>("/api/stats", fetcher, {
    refreshInterval: 30000,
  })

  const { data: evalData } = useSWR<EvalResults>("/api/eval_results.json", fetcher)

  const categoryData = evalData?.results
    ? Object.entries(
        evalData.results.reduce((acc: any, item) => {
          const cat = item.category
          if (!acc[cat]) acc[cat] = { correct: 0, total: 0 }
          acc[cat].total++
          if (item.correct) acc[cat].correct++
          return acc
        }, {})
      ).map(([name, stats]: [string, any]) => ({
        name: name.replace(/_/g, " "),
        accuracy: Math.round((stats.correct / stats.total) * 100),
      }))
    : []

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    setLoading(true)
    setResult(null)

    try {
      const response = await fetch("/api/query", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          prompt,
          session_id: `dashboard_${Date.now()}`,
        }),
      })
      const data = await response.json()
      setResult(data)
    } catch (error) {
      console.error("Error:", error)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="min-h-screen bg-gradient-to-br from-slate-950 via-slate-900 to-slate-950 p-6">
      <div className="max-w-7xl mx-auto space-y-6">
        {/* Header */}
        <div className="border-b border-primary/30 pb-4">
          <h1 className="text-4xl font-bold text-primary flex items-center gap-3">
            <Activity className="w-8 h-8" />
            LLM SENTINEL
          </h1>
          <p className="text-slate-400 mt-2">
            Real-time Hallucination Detection & Observability Platform
          </p>
        </div>

        {/* Stats Grid */}
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          <StatCard
            title="Total Traces"
            value={stats?.total_traces ?? "-"}
            icon={<Database className="w-5 h-5" />}
          />
          <StatCard
            title="Hallucinations"
            value={stats?.hallucinated_count ?? "-"}
            icon={<AlertCircle className="w-5 h-5" />}
            variant="danger"
          />
          <StatCard
            title="Detection Rate"
            value={stats ? `${stats.hallucination_rate}%` : "-"}
            icon={<TrendingUp className="w-5 h-5" />}
          />
          <StatCard
            title="Unique Sessions"
            value={stats?.unique_sessions ?? "-"}
            icon={<CheckCircle2 className="w-5 h-5" />}
            variant="success"
          />
        </div>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
          {/* Category Accuracy Chart */}
          <div className="bg-slate-900/50 border border-slate-800 rounded-lg p-6">
            <h2 className="text-xl font-bold text-primary mb-4">
              Evaluation Category Accuracy
            </h2>
            {categoryData.length > 0 ? (
              <ResponsiveContainer width="100%" height={300}>
                <BarChart data={categoryData}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#1e293b" />
                  <XAxis
                    dataKey="name"
                    stroke="#64748b"
                    angle={-45}
                    textAnchor="end"
                    height={100}
                    style={{ fontSize: "12px" }}
                  />
                  <YAxis stroke="#64748b" />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: "#0f172a",
                      border: "1px solid #334155",
                      borderRadius: "8px",
                    }}
                  />
                  <Bar dataKey="accuracy" radius={[8, 8, 0, 0]}>
                    {categoryData.map((entry, index) => (
                      <Cell
                        key={`cell-${index}`}
                        fill={entry.accuracy >= 70 ? "#10b981" : entry.accuracy >= 50 ? "#f59e0b" : "#ef4444"}
                      />
                    ))}
                  </Bar>
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <div className="h-[300px] flex items-center justify-center text-slate-500">
                Loading evaluation data...
              </div>
            )}
          </div>

          {/* Live Traces */}
          <div className="bg-slate-900/50 border border-slate-800 rounded-lg p-6">
            <h2 className="text-xl font-bold text-primary mb-4">Live Trace Log</h2>
            <div className="space-y-2 max-h-[300px] overflow-y-auto">
              {stats?.recent_traces.map((trace, i) => (
                <div
                  key={i}
                  className={`p-3 rounded border-l-4 ${
                    trace.is_hallucinated
                      ? "border-red-500 bg-red-500/10"
                      : "border-green-500 bg-green-500/10"
                  }`}
                >
                  <div className="flex items-center justify-between">
                    <span className="font-mono text-sm">
                      {trace.is_hallucinated ? "❌ HALLUCINATED" : "✅ GROUNDED"}
                    </span>
                    <span className="text-xs text-slate-500">
                      {new Date(trace.timestamp).toLocaleTimeString()}
                    </span>
                  </div>
                  <div className="text-xs text-slate-400 mt-1">{trace.session_id}</div>
                </div>
              ))}
            </div>
          </div>
        </div>

        {/* Try It Live */}
        <div className="bg-slate-900/50 border border-slate-800 rounded-lg p-6">
          <h2 className="text-xl font-bold text-primary mb-4">🔬 Try It Live</h2>
          <form onSubmit={handleSubmit} className="space-y-4">
            <textarea
              value={prompt}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="e.g., Explain the NeuroSync architecture from the 2025 NeurIPS paper..."
              className="w-full h-24 px-4 py-3 bg-slate-950 border border-slate-700 rounded-lg text-slate-100 placeholder-slate-500 focus:outline-none focus:border-primary resize-none"
            />
            <button
              type="submit"
              disabled={loading || !prompt.trim()}
              className="px-6 py-3 bg-primary text-primary-foreground font-bold rounded-lg hover:bg-primary/90 disabled:opacity-50 disabled:cursor-not-allowed transition-colors"
            >
              {loading ? "ANALYZING..." : "RUN DETECTION"}
            </button>
          </form>

          {result && (
            <div className="mt-6 p-4 rounded-lg border">
              <div
                className={`text-lg font-bold mb-3 ${
                  result.is_hallucinated ? "text-red-400" : "text-green-400"
                }`}
              >
                {result.is_hallucinated ? "❌ HALLUCINATION DETECTED" : "✅ RESPONSE GROUNDED"}
              </div>
              <p className="text-slate-300 mb-4">{result.response}</p>
              <div className="flex gap-6 text-sm text-slate-400 pt-4 border-t border-slate-700">
                <span>Sources: <strong>{result.sources_count}</strong></span>
                <span>Confidence: <strong>{(result.confidence_score ?? 0).toFixed(2)}</strong></span>
                <span>Stale: <strong>{result.is_stale ? "Yes" : "No"}</strong></span>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

function StatCard({
  title,
  value,
  icon,
  variant = "default",
}: {
  title: string
  value: string | number
  icon: React.ReactNode
  variant?: "default" | "success" | "danger"
}) {
  const colors = {
    default: "border-slate-700 bg-slate-900/50",
    success: "border-green-500/30 bg-green-500/10",
    danger: "border-red-500/30 bg-red-500/10",
  }

  return (
    <div className={`border rounded-lg p-4 ${colors[variant]}`}>
      <div className="flex items-center justify-between mb-2">
        <span className="text-sm text-slate-400">{title}</span>
        <div className="text-slate-400">{icon}</div>
      </div>
      <div className="text-3xl font-bold text-primary">{value}</div>
    </div>
  )
}
