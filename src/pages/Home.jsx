// src/pages/Home.jsx
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listarVisitas, deletarVisita } from '../lib/visitas'

function formatData(ts) {
  if (!ts) return '—'
  const d = ts.toDate?.() ?? new Date(ts)
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' })
}

export default function Home() {
  const navigate = useNavigate()
  const [visitas, setVisitas] = useState([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    listarVisitas().then(v => {
      setVisitas(v)
      setLoading(false)
    })
  }, [])

  async function remover(e, id) {
    e.stopPropagation()
    if (!confirm('Tem certeza que deseja remover esta vistoria?')) return
    try {
      await deletarVisita(id)
      setVisitas(prev => prev.filter(v => v.id !== id))
    } catch (err) {
      alert('Erro ao deletar: ' + err.message)
    }
  }

  return (
    <div className="min-h-screen bg-concreto-950 flex flex-col text-aco-200">
      
      {/* Header */}
      <header className="border-b border-concreto-700/60 bg-concreto-900/60 backdrop-blur-md sticky top-0 z-10 shadow-sm">
        <div className="max-w-6xl mx-auto px-6 py-4 flex items-center justify-between">
          <div>
            <p className="font-mono text-[10px] text-sinal-500 uppercase tracking-widest font-semibold">Tecnologia 360°</p>
            <h1 className="text-xl font-bold bg-clip-text text-transparent bg-gradient-to-r from-aco-100 to-aco-300 font-sans">
              Vistorias & Inspeções Obra360
            </h1>
          </div>
          <div className="flex items-center gap-3">
            <button
              onClick={() => navigate('/visita/demo')}
              className="bg-concreto-800 hover:bg-concreto-700 active:scale-[0.98] text-aco-200 border border-concreto-600/50 font-semibold text-xs px-4 py-2.5 rounded transition-all shadow-sm"
            >
              🧪 Modo Demo (Offline)
            </button>
            <button
              onClick={() => navigate('/upload')}
              className="bg-sinal-500 hover:bg-sinal-400 active:scale-[0.98] text-concreto-950 font-semibold text-xs px-4 py-2.5 rounded transition-all shadow-md shadow-sinal-500/10"
            >
              + Nova Vistoria
            </button>
          </div>
        </div>
      </header>

      {/* Main Content */}
      <main className="flex-1 max-w-6xl mx-auto w-full px-6 py-8">
        
        {loading && (
          <div className="flex flex-col items-center justify-center py-20 gap-3">
            <div className="w-6 h-6 border-2 border-aco-400 border-t-transparent rounded-full animate-spin" />
            <span className="font-mono text-xs text-aco-400">Buscando vistorias...</span>
          </div>
        )}

        {!loading && visitas.length === 0 && (
          <div className="text-center py-24 border border-dashed border-concreto-700/50 rounded-xl bg-concreto-900/10">
            <p className="text-aco-400 text-sm font-mono mb-4">Nenhuma vistoria ou pavimento cadastrado ainda.</p>
            <div className="flex justify-center gap-3">
              <button
                onClick={() => navigate('/upload')}
                className="bg-sinal-500 hover:bg-sinal-400 text-concreto-950 text-xs px-6 py-2.5 rounded font-semibold transition-all shadow"
              >
                Fazer Upload da Primeira Visita 360°
              </button>
              <button
                onClick={() => navigate('/visita/demo')}
                className="bg-concreto-800 hover:bg-concreto-700/80 text-aco-300 text-xs px-6 py-2.5 rounded border border-concreto-600/50 transition-all font-mono"
              >
                🧪 Testar Modo Demo (Offline)
              </button>
            </div>
          </div>
        )}

        <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-5">
          {visitas.map(v => (
            <div
              key={v.id}
              onClick={() => navigate(`/visita/${v.id}`)}
              className="group bg-concreto-900/60 border border-concreto-700/60 hover:border-sinal-500/40 rounded-xl p-4.5 cursor-pointer shadow-md hover:shadow-2xl hover:shadow-sinal-500/5 transition-all duration-300 hover:-translate-y-1 flex flex-col justify-between"
            >
              {/* Thumbnail Container */}
              <div className="relative w-full aspect-[16/10] bg-concreto-950 rounded-lg mb-4 overflow-hidden border border-concreto-850 flex items-center justify-center">
                {v.thumbnail_url ? (
                  <img
                    src={v.thumbnail_url}
                    alt={v.pavimento}
                    className="w-full h-full object-cover group-hover:scale-[1.03] transition-transform duration-500"
                    onError={e => { e.target.style.display = 'none' }}
                  />
                ) : (
                  <div className="flex flex-col items-center gap-1.5 opacity-60">
                    <span className="text-[20px]">📹</span>
                    <span className="text-aco-400 text-[10px] font-mono">sem visualização</span>
                  </div>
                )}
                
                {/* Floating Waypoint Count */}
                <div className="absolute top-2.5 left-2.5 bg-concreto-950/80 backdrop-blur border border-concreto-700/60 px-2 py-0.5 rounded text-[9px] font-mono text-aco-300">
                  {(v.waypoints || []).length} PONTOS
                </div>
              </div>

              <div>
                <p className="font-semibold text-aco-100 text-sm mb-1 group-hover:text-sinal-400 transition-colors">
                  {v.pavimento}
                </p>
                <p className="font-mono text-[10px] text-aco-400 flex items-center gap-1">
                  <span>📅</span> {formatData(v.data)}
                </p>
              </div>

              <div className="flex items-center justify-between mt-4.5 pt-3 border-t border-concreto-800/80">
                <span className="text-[10px] font-mono text-aco-400">
                  {v.duracao_segundos ? `${Math.floor(v.duracao_segundos / 60)}m ${v.duracao_segundos % 60}s` : 'sem duração'}
                </span>
                
                <div className="flex items-center gap-2.5">
                  <span className={`text-[9px] font-mono px-2 py-0.5 rounded-full border ${
                    v.status === 'ready'
                      ? 'bg-ok/10 border-ok/30 text-ok'
                      : 'bg-sinal-500/10 border-sinal-500/30 text-sinal-400'
                  }`}>
                    {v.status === 'ready' ? 'pronto' : 'processando'}
                  </span>
                  
                  <button
                    onClick={e => remover(e, v.id)}
                    className="text-alerta/65 hover:text-alerta text-xs transition-colors p-1"
                    title="Excluir Visita"
                  >
                    ✕
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </main>
    </div>
  )
}
