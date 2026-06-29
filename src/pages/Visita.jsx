// src/pages/Visita.jsx
import { useEffect, useState, useCallback } from 'react'
import { useParams, useNavigate } from 'react-router-dom'
import Player360 from '../components/Player360'
import PlantaViewer from '../components/PlantaViewer'
import WaypointEditor from '../components/WaypointEditor'
import { useVideoSync } from '../hooks/useVideoSync'
import { getVisita, atualizarVisita, listarVisitas } from '../lib/visitas'

export default function Visita() {
  const { id } = useParams()
  const navigate = useNavigate()
  const [visita, setVisita] = useState(null)
  const [waypoints, setWaypoints] = useState([])
  const [modoAdicionar, setModoAdicionar] = useState(false)
  const [pendente, setPendente] = useState(null) // { x, y }
  const [salvando, setSalvando] = useState(false)
  const [toast, setToast] = useState(null)

  // Estados adicionais para calibração, sobreposição e azimute
  const [headingOffset, setHeadingOffset] = useState(0)
  const [modoCalibrarAncoras, setModoCalibrarAncoras] = useState(null) // null | 'ancora1' | 'ancora2'
  const [ancora1, setAncora1] = useState(null)
  const [ancora2, setAncora2] = useState(null)
  const [visitaSobrepostaId, setVisitaSobrepostaId] = useState(null)
  const [visitaSobreposta, setVisitaSobreposta] = useState(null)
  const [listaVisitas, setListaVisitas] = useState([])

  // Estados para trajetória rápida
  const [modoTrajetoriaRapida, setModoTrajetoriaRapida] = useState(false)
  const [etapaTrajetoriaRapida, setEtapaTrajetoriaRapida] = useState(null) // 'inicio' | 'fim'
  const [trajetoriaRapidaPontoA, setTrajetoriaRapidaPontoA] = useState(null)

  const {
    tempoAtual, duracao, posicao, waypointAtivo, player,
    registrarPlayer, pularParaWaypoint, pularParaCoordenada,
  } = useVideoSync(waypoints)

  // Carrega lista de visitas para sobreposição (exclui a visita atual)
  useEffect(() => {
    listarVisitas().then(vList => {
      setListaVisitas(vList.filter(v => v.id !== id))
    })
  }, [id])

  // Carrega dados da visita principal
  useEffect(() => {
    getVisita(id).then(v => {
      if (!v) { navigate('/'); return }
      setVisita(v)
      setWaypoints(v.waypoints || [])
      setHeadingOffset(v.heading_offset || 0)
      setAncora1(v.ancora1 || null)
      setAncora2(v.ancora2 || null)
    })
  }, [id, navigate])

  // Monitora seleção de planta sobreposta
  useEffect(() => {
    if (!visitaSobrepostaId) {
      setVisitaSobreposta(null)
      return
    }
    const found = listaVisitas.find(v => v.id === visitaSobrepostaId)
    if (found) {
      setVisitaSobreposta(found)
    }
  }, [visitaSobrepostaId, listaVisitas])

  function mostrarToast(msg, tipo = 'ok') {
    setToast({ msg, tipo })
    setTimeout(() => setToast(null), 3000)
  }

  // Clique na planta (Canvas)
  const handleClickCoordenada = useCallback((x, y) => {
    // 1. Calibração de Âncoras
    if (modoCalibrarAncoras) {
      if (modoCalibrarAncoras === 'ancora1') {
        setAncora1({ x, y })
        setModoCalibrarAncoras(null)
        mostrarToast('Âncora A definida! Agora defina a Âncora B se necessário.')
      } else if (modoCalibrarAncoras === 'ancora2') {
        setAncora2({ x, y })
        setModoCalibrarAncoras(null)
        mostrarToast('Âncora B definida!')
      }
      return
    }

    // 2. Trajetória Rápida
    if (modoTrajetoriaRapida) {
      if (etapaTrajetoriaRapida === 'inicio') {
        setTrajetoriaRapidaPontoA({ x, y })
        setEtapaTrajetoriaRapida('fim')
        mostrarToast('Início marcado. Clique na planta para marcar o FIM da caminhada.')
      } else if (etapaTrajetoriaRapida === 'fim') {
        const tFim = Math.round(duracao || player?.duration() || 100)
        const novoInicio = {
          t: 0,
          x: trajetoriaRapidaPontoA.x,
          y: trajetoriaRapidaPontoA.y,
          label: 'Início da Caminhada',
          observacao: 'Gerado via Trajetória Rápida'
        }
        const novoFim = {
          t: tFim,
          x,
          y,
          label: 'Fim da Caminhada',
          observacao: 'Gerado via Trajetória Rápida'
        }
        setWaypoints([novoInicio, novoFim])
        setModoTrajetoriaRapida(false)
        setEtapaTrajetoriaRapida(null)
        setTrajetoriaRapidaPontoA(null)
        mostrarToast('Trajetória criada. Ajuste o Norte no slider de bússola.')
      }
      return
    }

    // 3. Adicionar waypoint normal
    if (modoAdicionar && !pendente) {
      setPendente({ x, y })
    } else if (!modoAdicionar) {
      pularParaCoordenada(x, y)
    }
  }, [modoCalibrarAncoras, modoTrajetoriaRapida, etapaTrajetoriaRapida, duracao, player, trajetoriaRapidaPontoA, modoAdicionar, pendente, pularParaCoordenada])

  function confirmarPendente({ label, observacao }) {
    const novo = { t: Math.round(tempoAtual), x: pendente.x, y: pendente.y, label, observacao }
    setWaypoints(prev => [...prev, novo])
    setPendente(null)
    setModoAdicionar(false)
  }

  function removerWaypoint(index) {
    const sorted = [...waypoints].sort((a, b) => a.t - b.t)
    sorted.splice(index, 1)
    setWaypoints(sorted)
  }

  async function salvar() {
    setSalvando(true)
    try {
      await atualizarVisita(id, {
        waypoints,
        heading_offset: headingOffset,
        ancora1,
        ancora2,
      })
      mostrarToast('Alterações salvas com sucesso!')
    } catch (e) {
      mostrarToast('Erro ao salvar no banco', 'erro')
    } finally {
      setSalvando(false)
    }
  }

  function handleToggleCalibrarAncoras(tipo) {
    if (modoCalibrarAncoras === tipo) {
      setModoCalibrarAncoras(null)
    } else {
      setModoCalibrarAncoras(tipo)
      setModoAdicionar(false)
      setModoTrajetoriaRapida(false)
      setPendente(null)
      mostrarToast(`Clique no mapa para definir a ${tipo === 'ancora1' ? 'Âncora A' : 'Âncora B'}`)
    }
  }

  function handleLimparAncoras() {
    setAncora1(null)
    setAncora2(null)
    mostrarToast('Âncoras deste pavimento limpas.')
  }

  function handleToggleModoTrajetoriaRapida() {
    if (modoTrajetoriaRapida) {
      setModoTrajetoriaRapida(false)
      setEtapaTrajetoriaRapida(null)
      setTrajetoriaRapidaPontoA(null)
    } else {
      setModoTrajetoriaRapida(true)
      setEtapaTrajetoriaRapida('inicio')
      setModoAdicionar(false)
      setModoCalibrarAncoras(null)
      setPendente(null)
      mostrarToast('Clique no mapa para definir o ponto de INÍCIO da caminhada (0:00).')
    }
  }

  if (!visita) {
    return (
      <div className="min-h-screen bg-concreto-950 flex items-center justify-center">
        <div className="flex flex-col items-center gap-3">
          <div className="w-8 h-8 border-4 border-sinal-500 border-t-transparent rounded-full animate-spin" />
          <span className="font-mono text-xs text-aco-400">Acessando pavimento...</span>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen bg-concreto-950 flex flex-col text-aco-200">
      
      {/* Topbar */}
      <header className="h-14 bg-concreto-900 border-b border-concreto-700/80 flex items-center justify-between px-6 shrink-0 shadow-lg">
        <div className="flex items-center gap-4">
          <button
            onClick={() => navigate('/')}
            className="text-aco-400 hover:text-aco-200 text-xs font-mono transition-colors flex items-center gap-1.5 px-2.5 py-1.5 rounded bg-concreto-800/40 hover:bg-concreto-800"
          >
            ← Painel
          </button>
          <div className="h-5 w-px bg-concreto-700" />
          <div>
            <h2 className="text-sm font-semibold text-aco-100 font-sans tracking-wide leading-none">{visita.pavimento}</h2>
            <span className="font-mono text-[10px] text-aco-400">
              {visita.data?.toDate?.()?.toLocaleDateString('pt-BR', { day: '2-digit', month: 'short', year: 'numeric' }) ?? ''}
            </span>
          </div>
        </div>

        {/* Notificações (Toast embutido) */}
        {toast && (
          <div className={`font-mono text-xs px-4 py-2 rounded-md shadow-md animate-fade-in transition-all ${
            toast.tipo === 'ok'
              ? 'bg-ok/10 border border-ok/30 text-ok'
              : 'bg-alerta/10 border border-alerta/30 text-alerta'
          }`}>
            {toast.msg}
          </div>
        )}

        <div className="flex items-center gap-3">
          <span className={`text-[10px] font-mono px-2 py-0.5 rounded-full border ${
            visita.status === 'ready'
              ? 'bg-ok/10 border-ok/30 text-ok'
              : 'bg-sinal-500/10 border-sinal-500/30 text-sinal-400'
          }`}>
            {visita.status === 'ready' ? 'ativo' : 'processando'}
          </span>
        </div>
      </header>

      {/* Layout principal */}
      <main className="flex-1 grid grid-cols-[1.1fr_1fr_310px] gap-3.5 p-3.5 min-h-0">
        
        {/* Coluna 1: Player 360° */}
        <div className="flex flex-col gap-2.5 min-h-0">
          <div className="flex items-center justify-between">
            <p className="font-mono text-xs text-aco-400 uppercase tracking-widest">Vídeo 360° interativo</p>
            <span className="text-[10px] text-aco-400 font-mono">Arraste para rotacionar a câmera</span>
          </div>
          <div className="flex-1 bg-concreto-900 rounded-lg overflow-hidden border border-concreto-700/80 shadow-md">
            <Player360
              hlsUrl={visita.hls_url}
              onReady={registrarPlayer}
              autoplay={false}
            />
          </div>
        </div>

        {/* Coluna 2: Planta e Bússola */}
        <div className="flex flex-col gap-2.5 min-h-0">
          <div className="flex items-center justify-between">
            <p className="font-mono text-xs text-aco-400 uppercase tracking-widest">
              Planta baixa — {
                modoCalibrarAncoras
                  ? <span className="text-blue-400 animate-pulse">clique para marcar {modoCalibrarAncoras === 'ancora1' ? 'Âncora A' : 'Âncora B'}</span>
                  : modoTrajetoriaRapida
                    ? <span className="text-sinal-400 animate-pulse">modo trajetória rápida</span>
                    : modoAdicionar
                      ? <span className="text-sinal-400">modo marcação</span>
                      : 'clique para navegar no tempo'
              }
            </p>
          </div>
          
          <div className="flex-1 min-h-0 relative">
            <PlantaViewer
              plantaUrl={visita.planta_url}
              waypoints={waypoints}
              posicao={posicao}
              waypointAtivo={waypointAtivo}
              onClickCoordenada={handleClickCoordenada}
              onClickWaypoint={pularParaWaypoint}
              player={player}
              headingOffset={headingOffset}
              modoCalibrarAncoras={modoCalibrarAncoras}
              ancora1={ancora1}
              ancora2={ancora2}
              visitaSobreposta={visitaSobreposta}
            />
          </div>

          {/* Slider de Calibração da Bússola */}
          <div className="bg-concreto-900 border border-concreto-700/70 rounded-lg p-3 flex flex-col gap-2 shadow-md shrink-0">
            <div className="flex justify-between items-center text-xs font-mono">
              <span className="text-aco-300 font-medium">Bússola (Alinhamento do Norte)</span>
              <span className="text-sinal-400 font-bold bg-sinal-500/10 px-2 py-0.5 rounded border border-sinal-500/20">{headingOffset}°</span>
            </div>
            <input
              type="range"
              min="-180"
              max="180"
              value={headingOffset}
              onChange={e => setHeadingOffset(parseInt(e.target.value))}
              className="w-full h-1 bg-concreto-800 rounded-lg appearance-none cursor-pointer accent-sinal-500 border border-concreto-700/40"
            />
            <p className="text-[10px] text-aco-400 font-mono leading-relaxed">
              Arraste o controle acima para girar o cone azul de visão no mapa, alinhando-o com a direção do vídeo.
            </p>
          </div>
        </div>

        {/* Coluna 3: Painel Lateral / Editor */}
        <div className="flex flex-col gap-2.5 min-h-0">
          <p className="font-mono text-xs text-aco-400 uppercase tracking-widest">Painel de controle</p>
          <div className="flex-1 min-h-0">
            <WaypointEditor
              waypoints={waypoints}
              tempoAtual={tempoAtual}
              duracao={duracao}
              modoAdicionar={modoAdicionar}
              onToggleModo={() => { setModoAdicionar(v => !v); setPendente(null) }}
              onRemover={removerWaypoint}
              onSalvar={salvar}
              onClickWaypoint={pularParaWaypoint}
              pendente={pendente}
              onConfirmarPendente={confirmarPendente}
              onCancelarPendente={() => { setPendente(null); setModoAdicionar(false) }}

              // Âncoras
              modoCalibrarAncoras={modoCalibrarAncoras}
              onToggleCalibrarAncoras={handleToggleCalibrarAncoras}
              ancora1={ancora1}
              ancora2={ancora2}
              onLimparAncoras={handleLimparAncoras}

              // Sobreposição
              listaVisitas={listaVisitas}
              visitaSobrepostaId={visitaSobrepostaId}
              onSelectVisitaSobreposta={setVisitaSobrepostaId}

              // Trajetória rápida
              modoTrajetoriaRapida={modoTrajetoriaRapida}
              onToggleModoTrajetoriaRapida={handleToggleModoTrajetoriaRapida}
              etapaTrajetoriaRapida={etapaTrajetoriaRapida}
            />
          </div>
        </div>

      </main>
    </div>
  )
}
