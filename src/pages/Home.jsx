// src/pages/Home.jsx
import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { listarVisitas, deletarVisita, excluirVisitaCompleta } from '../lib/visitas'

function formatData(ts) {
  if (!ts) return '—'
  const d = ts.toDate?.() ?? new Date(ts)
  return d.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' })
}

export default function Home() {
  const navigate = useNavigate()
  const [visitas, setVisitas] = useState([])
  const [loading, setLoading] = useState(true)
  // Modal de exclusao (2026-07-17): substitui o confirm() nativo. Fluxo:
  // escolhe a vistoria -> modal explica O QUE sera apagado (registro +
  // panoramas/mapa/video no storage) -> excluirVisitaCompleta() limpa o R2
  // via API da VPS e depois o doc do Firestore. Se a limpeza do storage
  // falhar (API fora do ar etc.), o modal mostra o erro e oferece excluir
  // SO o registro - escolha explicita, nunca silenciosa.
  const [excluindo, setExcluindo] = useState(null)      // vistoria alvo do modal
  const [excluindoBusy, setExcluindoBusy] = useState(false)
  const [excluirErro, setExcluirErro] = useState('')
  const apiMedicaoUrl = import.meta.env.VITE_API_MEDICAO_URL || null
  const apiMedicaoKey = import.meta.env.VITE_MEDICAO_API_KEY || null

  useEffect(() => {
    listarVisitas().then(v => {
      setVisitas(v)
      setLoading(false)
    })
  }, [])

  function abrirExclusao(e, v) {
    e.stopPropagation()
    setExcluirErro('')
    setExcluindo(v)
  }

  async function confirmarExclusao(soRegistro = false) {
    if (!excluindo) return
    setExcluindoBusy(true)
    setExcluirErro('')
    try {
      if (soRegistro) {
        await deletarVisita(excluindo.id)
      } else {
        await excluirVisitaCompleta(excluindo, {
          apiUrl: apiMedicaoUrl, apiKey: apiMedicaoKey,
        })
      }
      setVisitas(prev => prev.filter(x => x.id !== excluindo.id))
      setExcluindo(null)
    } catch (err) {
      setExcluirErro(err.message || String(err))
    } finally {
      setExcluindoBusy(false)
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
                    onClick={e => abrirExclusao(e, v)}
                    className="text-alerta/65 hover:text-alerta text-xs transition-colors p-1"
                    title="Excluir vistoria"
                  >
                    🗑
                  </button>
                </div>
              </div>
            </div>
          ))}
        </div>
      </main>

      {/* Modal de exclusao de vistoria */}
      {excluindo && (
        <div
          className="fixed inset-0 z-50 bg-black/70 backdrop-blur-sm flex items-center justify-center p-4"
          onClick={() => !excluindoBusy && setExcluindo(null)}
        >
          <div
            className="bg-concreto-900 border border-concreto-700 rounded-xl max-w-md w-full p-5 shadow-2xl"
            onClick={e => e.stopPropagation()}
          >
            <h3 className="text-alerta font-semibold text-sm font-mono mb-1">
              Excluir vistoria
            </h3>
            <p className="text-aco-200 text-sm mb-3">
              <span className="font-semibold">{excluindo.pavimento || 'Sem nome'}</span>
              <span className="text-aco-400 text-xs"> — {formatData(excluindo.data)}</span>
            </p>
            <div className="bg-concreto-800/60 border border-concreto-700/60 rounded-lg p-3 mb-3">
              <p className="text-aco-300 text-xs leading-relaxed">
                Isto apaga <span className="text-alerta font-semibold">permanentemente</span>:
              </p>
              <ul className="text-aco-400 text-[11px] font-mono mt-1.5 space-y-0.5">
                <li>• o registro da vistoria (trajetória, calibração, ambientes)</li>
                <li>• as fotos 360°, miniaturas e o mapa 3D no armazenamento</li>
                {excluindo.video_r2_key && (
                  <li>• o vídeo bruto enviado ({excluindo.video_r2_key.split('/').pop()})</li>
                )}
              </ul>
            </div>
            {excluirErro && (
              <div className="bg-alerta/10 border border-alerta/40 rounded-lg p-3 mb-3">
                <p className="text-alerta text-xs leading-relaxed">{excluirErro}</p>
                <button
                  onClick={() => confirmarExclusao(true)}
                  disabled={excluindoBusy}
                  className="mt-2 text-[11px] font-mono underline text-aco-300 hover:text-aco-100 disabled:opacity-50"
                >
                  Excluir só o registro (deixa os arquivos no armazenamento)
                </button>
              </div>
            )}
            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setExcluindo(null)}
                disabled={excluindoBusy}
                className="px-4 py-2 text-xs font-mono rounded-lg border border-concreto-700 text-aco-300 hover:bg-concreto-800 disabled:opacity-50 transition-colors"
              >
                Cancelar
              </button>
              <button
                onClick={() => confirmarExclusao(false)}
                disabled={excluindoBusy}
                className="px-4 py-2 text-xs font-mono rounded-lg bg-alerta/85 hover:bg-alerta text-white disabled:opacity-50 transition-colors"
              >
                {excluindoBusy ? 'Excluindo...' : 'Excluir definitivamente'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
