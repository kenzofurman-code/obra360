// src/components/WaypointEditor.jsx
import { useState } from 'react'

function formatTempo(s) {
  const m = Math.floor(s / 60)
  const ss = Math.floor(s % 60)
  return `${m}:${ss.toString().padStart(2, '0')}`
}

export default function WaypointEditor({
  waypoints = [],
  tempoAtual,
  duracao,
  modoAdicionar,
  onToggleModo,
  onRemover,
  onSalvar,
  onClickWaypoint,
  pendente,
  onConfirmarPendente,
  onCancelarPendente,

  // Âncoras
  modoCalibrarAncoras,
  onToggleCalibrarAncoras,
  ancora1,
  ancora2,
  onLimparAncoras,

  // Sobreposição
  listaVisitas = [],
  visitaSobrepostaId,
  onSelectVisitaSobreposta,

  // Trajetória rápida
  modoTrajetoriaRapida,
  onToggleModoTrajetoriaRapida,
  etapaTrajetoriaRapida,
}) {
  const [activeTab, setActiveTab] = useState('caminhada') // 'caminhada' | 'sobreposicao'
  const [label, setLabel] = useState('')
  const [obs, setObs] = useState('')

  const sorted = [...waypoints].sort((a, b) => a.t - b.t)

  function confirmar() {
    if (!label.trim()) return
    onConfirmarPendente({ label: label.trim(), observacao: obs.trim() })
    setLabel('')
    setObs('')
  }

  return (
    <div className="flex flex-col h-full bg-concreto-900 border border-concreto-700 rounded-lg overflow-hidden">
      
      {/* Tabs */}
      <div className="flex border-b border-concreto-700 bg-concreto-950/40">
        <button
          onClick={() => setActiveTab('caminhada')}
          className={`flex-1 py-2.5 text-xs font-mono font-medium border-b-2 transition-all ${
            activeTab === 'caminhada'
              ? 'border-sinal-500 text-aco-200'
              : 'border-transparent text-aco-400 hover:text-aco-200'
          }`}
        >
          Caminhada
        </button>
        <button
          onClick={() => setActiveTab('sobreposicao')}
          className={`flex-1 py-2.5 text-xs font-mono font-medium border-b-2 transition-all ${
            activeTab === 'sobreposicao'
              ? 'border-sinal-500 text-aco-200'
              : 'border-transparent text-aco-400 hover:text-aco-200'
          }`}
        >
          Sobreposição
        </button>
      </div>

      {/* Tab 1: Caminhada */}
      {activeTab === 'caminhada' && (
        <div className="flex flex-col flex-1 min-h-0">
          {/* Tempo atual */}
          <div className="px-4 py-2.5 bg-concreto-800/50 flex items-center justify-between text-xs font-mono border-b border-concreto-700/60">
            <span className="text-aco-400">tempo atual</span>
            <span className="text-sinal-400 font-semibold">{formatTempo(tempoAtual)}</span>
          </div>

          {/* Trajetória Rápida */}
          {!pendente && (
            <div className="px-3 py-2 bg-concreto-800/30 border-b border-concreto-700/40 space-y-1.5">
              {modoTrajetoriaRapida ? (
                <div className="p-2 bg-sinal-500/10 border border-sinal-500/30 rounded text-center space-y-2">
                  <p className="text-[11px] font-mono text-sinal-400">
                    {etapaTrajetoriaRapida === 'inicio'
                      ? '① Clique na planta no ponto de INÍCIO (0:00)'
                      : '② Clique na planta no ponto de TÉRMINO'
                    }
                  </p>
                  <button
                    onClick={onToggleModoTrajetoriaRapida}
                    className="w-full bg-concreto-700 hover:bg-concreto-600 text-aco-200 text-[10px] py-1 rounded transition-colors"
                  >
                    Cancelar Trajetória Rápida
                  </button>
                </div>
              ) : (
                waypoints.length === 0 && (
                  <button
                    onClick={onToggleModoTrajetoriaRapida}
                    className="w-full bg-sinal-500/15 hover:bg-sinal-500/25 border border-sinal-500/30 text-sinal-400 font-mono text-[11px] py-2 rounded transition-all"
                  >
                    ⚡ Trajetória Rápida (Início → Fim)
                  </button>
                )
              )}
            </div>
          )}

          {/* Formulário de Waypoint Pendente */}
          {pendente && (
            <div className="mx-3 my-2 p-3 bg-sinal-500/10 border border-sinal-500/30 rounded-lg shrink-0">
              <p className="text-[11px] font-mono text-sinal-400 mb-2">
                Ponto em {formatTempo(tempoAtual)} · ({(pendente.x * 100).toFixed(0)}%, {(pendente.y * 100).toFixed(0)}%)
              </p>
              <input
                className="w-full bg-concreto-800 border border-concreto-600 rounded px-2.5 py-1.5 text-xs text-aco-200 placeholder-aco-400 mb-2 focus:outline-none focus:border-sinal-500"
                placeholder="Identificação (ex: Hall Elevadores)"
                value={label}
                onChange={e => setLabel(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && confirmar()}
                autoFocus
              />
              <input
                className="w-full bg-concreto-800 border border-concreto-600 rounded px-2.5 py-1.5 text-xs text-aco-200 placeholder-aco-400 mb-2.5 focus:outline-none focus:border-sinal-500"
                placeholder="Observação (opcional)"
                value={obs}
                onChange={e => setObs(e.target.value)}
              />
              <div className="flex gap-2">
                <button
                  onClick={confirmar}
                  disabled={!label.trim()}
                  className="flex-1 bg-sinal-500 hover:bg-sinal-400 disabled:opacity-40 text-concreto-950 font-semibold text-[11px] py-1.5 rounded transition-all"
                >
                  Adicionar
                </button>
                <button
                  onClick={() => { onCancelarPendente(); setLabel(''); setObs('') }}
                  className="flex-1 bg-concreto-700 hover:bg-concreto-600 text-aco-300 text-[11px] py-1.5 rounded transition-all"
                >
                  Cancelar
                </button>
              </div>
            </div>
          )}

          {/* Adicionar Waypoint Normal */}
          {!pendente && !modoTrajetoriaRapida && (
            <div className="px-3 py-2 shrink-0">
              <button
                onClick={onToggleModo}
                className={`w-full py-2 rounded text-xs font-mono font-medium transition-all ${
                  modoAdicionar
                    ? 'bg-sinal-500/20 border border-sinal-500 text-sinal-400'
                    : 'bg-concreto-700 hover:bg-concreto-600 text-aco-300'
                }`}
              >
                {modoAdicionar ? '● Clique no mapa para marcar' : '+ Adicionar Waypoint Intermediário'}
              </button>
            </div>
          )}

          {/* Lista de Waypoints */}
          <div className="flex-1 overflow-y-auto px-3 py-1.5 space-y-1">
            {sorted.length === 0 && !modoTrajetoriaRapida && (
              <p className="text-center text-xs text-aco-400 font-mono py-8 leading-relaxed">
                Nenhum ponto registrado.<br />Use a **Trajetória Rápida**<br />para criar o início e o fim,<br />ou marque manualmente.
              </p>
            )}
            {sorted.map((wp, idx) => (
              <div
                key={idx}
                onClick={() => onClickWaypoint(wp)}
                className="flex items-start gap-2 p-2 rounded cursor-pointer hover:bg-concreto-800 group transition-all"
              >
                <span className="font-mono text-xs text-sinal-500 pt-0.5 w-10 shrink-0">
                  {formatTempo(wp.t)}
                </span>
                <div className="flex-1 min-w-0">
                  <p className="text-xs text-aco-200 font-medium truncate">{wp.label}</p>
                  {wp.observacao && (
                    <p className="text-[11px] text-aco-400 truncate">{wp.observacao}</p>
                  )}
                </div>
                <button
                  onClick={e => { e.stopPropagation(); onRemover(idx) }}
                  className="opacity-0 group-hover:opacity-100 text-alerta text-xs px-1.5 transition-opacity"
                >
                  ✕
                </button>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Tab 2: Sobreposição */}
      {activeTab === 'sobreposicao' && (
        <div className="flex-1 overflow-y-auto p-4 space-y-5">
          {/* Calibração de Âncoras */}
          <div className="space-y-2">
            <h3 className="text-xs font-semibold text-aco-200 uppercase tracking-wider">1. Calibrar Âncoras A/B</h3>
            <p className="text-[10px] text-aco-400 leading-relaxed">
              Defina dois pontos coincidentes nas plantas (ex: elevadores ou colunas) para alinhá-las perfeitamente.
            </p>

            <div className="grid grid-cols-2 gap-2 pt-1">
              <button
                onClick={() => onToggleCalibrarAncoras('ancora1')}
                className={`py-2 text-[10px] font-mono border rounded transition-all ${
                  modoCalibrarAncoras === 'ancora1'
                    ? 'bg-blue-500/20 border-blue-400 text-blue-400 font-bold'
                    : ancora1
                      ? 'bg-concreto-800 border-blue-500/60 text-blue-400'
                      : 'bg-concreto-800 border-concreto-600 text-aco-400 hover:text-aco-200'
                }`}
              >
                {ancora1 ? '✓ Âncora A Definida' : 'Definir Âncora A'}
              </button>

              <button
                onClick={() => onToggleCalibrarAncoras('ancora2')}
                className={`py-2 text-[10px] font-mono border rounded transition-all ${
                  modoCalibrarAncoras === 'ancora2'
                    ? 'bg-red-500/20 border-red-400 text-red-400 font-bold'
                    : ancora2
                      ? 'bg-concreto-800 border-red-500/60 text-red-400'
                      : 'bg-concreto-800 border-concreto-600 text-aco-400 hover:text-aco-200'
                }`}
              >
                {ancora2 ? '✓ Âncora B Definida' : 'Definir Âncora B'}
              </button>
            </div>

            {(ancora1 || ancora2) && (
              <button
                onClick={onLimparAncoras}
                className="w-full text-[10px] font-mono text-alerta hover:underline text-left pt-1"
              >
                ✕ Limpar âncoras deste pavimento
              </button>
            )}
          </div>

          <hr className="border-concreto-700/60" />

          {/* Seleção de Pavimento para Sobrepor */}
          <div className="space-y-2">
            <h3 className="text-xs font-semibold text-aco-200 uppercase tracking-wider">2. Sobreposição de Plantas</h3>
            <p className="text-[10px] text-aco-400 leading-relaxed">
              Selecione outro pavimento para sobrepor de forma semitransparente neste mapa.
            </p>

            <select
              value={visitaSobrepostaId || ''}
              onChange={e => onSelectVisitaSobreposta(e.target.value || null)}
              className="w-full bg-concreto-800 border border-concreto-600 rounded px-2.5 py-2 text-xs text-aco-200 focus:outline-none focus:border-sinal-500"
            >
              <option value="">(Nenhum pavimento)</option>
              {listaVisitas.map(v => (
                <option key={v.id} value={v.id}>{v.pavimento}</option>
              ))}
            </select>

            {visitaSobrepostaId && (
              <div className="p-2.5 bg-concreto-800/40 border border-concreto-700 rounded text-[10px] text-aco-400 leading-relaxed space-y-1">
                <span className="font-semibold text-aco-300 block">Requisitos de alinhamento:</span>
                <p>1. Âncoras A/B definidas nesta planta.</p>
                <p>2. Âncoras A/B definidas na planta sobreposta.</p>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Salvar */}
      <div className="px-3 py-3 border-t border-concreto-700 bg-concreto-950/20">
        <button
          onClick={onSalvar}
          className="w-full bg-ok hover:bg-ok/90 active:scale-[0.98] text-concreto-950 font-semibold text-xs py-2.5 rounded transition-all"
        >
          Salvar Alterações
        </button>
      </div>
    </div>
  )
}
