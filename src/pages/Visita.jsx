// src/pages/Visita.jsx
import { useEffect, useState, useCallback, useMemo } from 'react'
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

  // Layout Interativo
  const [tamanhoMapa, setTamanhoMapa] = useState('md') // 'sm' | 'md' | 'lg' | 'minimized'
  const [menuAberto, setMenuAberto] = useState(false) // se a gaveta lateral de edição está aberta

  // Controles do Vídeo e Ajustes da Passarela
  const [tocando, setTocando] = useState(false)
  const [lineOpacity, setLineOpacity] = useState(79) // 79% (conforme mockup)
  const [lineThickness, setLineThickness] = useState(0.6) // 0.6x (conforme mockup)
  const [mostrarConfigPopup, setMostrarConfigPopup] = useState(false)
  const [velocidade, setVelocidade] = useState(1) // 1x, 1.5x, 2x

  // Estados adicionais para calibração, sobreposição e azimute
  const [headingOffset, setHeadingOffset] = useState(0)
  const [modoCalibrarAncoras, setModoCalibrarAncoras] = useState(null) // null | 'ancora1' | 'ancora2'
  const [ancora1, setAncora1] = useState(null)
  const [ancora2, setAncora2] = useState(null)
  const [isImported, setIsImported] = useState(false)
  const [pathScale, setPathScale] = useState(0.50)
  const [espelharCaminho, setEspelharCaminho] = useState(true)
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

  // Função para alinhar as coordenadas relativas da trajetória na planta baixa
  const alinharPonto = useCallback((pt) => {
    if (!pt) return null

    // 1. Alinhamento por 2 âncoras (Procrustes 2D) - SÓ para trajetória IMPORTADA
    //    sem passar pelo Map Matching do backend. processar_vistoria.py/worker.py já
    //    fazem sua própria correção (âncora + snap nas portas do PDF) antes de salvar;
    //    aplicar aqui uma 2ª calibração por Procrustes (que deriva escala/rotação
    //    comparando 1º/último ponto com âncora A/B) desfaria essa correção.
    if (isImported && ancora1 && ancora2 && waypoints.length > 1) {
      const sorted = [...waypoints].sort((a, b) => a.t - b.t)
      const W1 = sorted[0]
      const W2 = sorted[sorted.length - 1]

      const dWx = W2.x - W1.x
      const dWy = W2.y - W1.y
      const distW = Math.sqrt(dWx * dWx + dWy * dWy)

      const dAx = ancora2.x - ancora1.x
      const dAy = ancora2.y - ancora1.y
      const distA = Math.sqrt(dAx * dAx + dAy * dAy)

      if (distW > 0 && distA > 0) {
        const scale = distA / distW
        const angleW = Math.atan2(dWy, dWx)
        const angleA = Math.atan2(dAy, dAx)
        const rotation = angleA - angleW

        const rawDx = pt.x - W1.x
        const dx = espelharCaminho ? -rawDx : rawDx
        const dy = -(pt.y - W1.y) // Inverte Y para subir na planta ao andar para frente

        const rx = (dx * Math.cos(rotation) - dy * Math.sin(rotation)) * scale
        const ry = (dx * Math.sin(rotation) + dy * Math.cos(rotation)) * scale

        return {
          ...pt,
          x: ancora1.x + rx,
          y: ancora1.y + ry
        }
      }
    }

    // 2. Alinhamento por 1 âncora + Escala + Giro da Bússola - MESMA transformação
    //    que o backend usa (alinhar_ponto em processar_vistoria.py). Aplica SEMPRE
    //    que houver âncora A, seja trajetória importada ou vinda do worker.py -
    //    é o que garante que o site mostre exatamente o que o backend calibrou
    //    (incluindo a correção por porta), em vez de coordenadas brutas da odometria.
    if (ancora1) {
      const theta = ((headingOffset + 180) * Math.PI) / 180

      const dx = espelharCaminho ? -pt.x : pt.x
      const dy = -pt.y

      const rx = dx * Math.cos(theta) - dy * Math.sin(theta)
      const ry = dx * Math.sin(theta) + dy * Math.cos(theta)

      return {
        ...pt,
        x: ancora1.x + rx * pathScale,
        y: ancora1.y + ry * pathScale
      }
    }

    // 3. Sem nenhuma âncora: Centraliza a trajetória no meio do mapa (escala inicial provisória)
    if (waypoints.length > 0) {
      const sorted = [...waypoints].sort((a, b) => a.t - b.t)
      const W1 = sorted[0]
      const rawDx = pt.x - W1.x
      const dx = espelharCaminho ? -rawDx : rawDx
      const dy = -(pt.y - W1.y) // Inverte Y para subir na planta ao andar para frente
      return {
        ...pt,
        x: 0.5 + dx * pathScale,
        y: 0.5 + dy * pathScale
      }
    }

    return pt
  }, [waypoints, isImported, ancora1, ancora2, headingOffset, pathScale, espelharCaminho])

  // Realiza o inverso do alinhamento: converte coordenadas [0, 1] da planta para a escala/giro bruto do Python
  const desalinharPonto = useCallback((pt) => {
    if (!pt) return null

    // 1. Inverso por 2 âncoras (Procrustes 2D) - só para trajetória IMPORTADA (ver
    //    mesmo comentário em alinharPonto acima)
    if (isImported && ancora1 && ancora2 && waypoints.length > 1) {
      const sorted = [...waypoints].sort((a, b) => a.t - b.t)
      const W1 = sorted[0]
      const W2 = sorted[sorted.length - 1]

      const dWx = W2.x - W1.x
      const dWy = W2.y - W1.y
      const distW = Math.sqrt(dWx * dWx + dWy * dWy)

      const dAx = ancora2.x - ancora1.x
      const dAy = ancora2.y - ancora1.y
      const distA = Math.sqrt(dAx * dAx + dAy * dAy)

      if (distW > 0 && distA > 0) {
        const scale = distA / distW
        const angleW = Math.atan2(dWy, dWx)
        const angleA = Math.atan2(dAy, dAx)
        const rotation = angleA - angleW
        const invRotation = -rotation

        const dx = pt.x - ancora1.x
        const dy = pt.y - ancora1.y

        // Aplica a rotação inversa no vetor de diferença
        const rx = (dx * Math.cos(invRotation) - dy * Math.sin(invRotation)) / scale
        const ry = (dx * Math.sin(invRotation) + dy * Math.cos(invRotation)) / scale

        // Desfaz o espelhamento horizontal se ativado
        const finalRx = espelharCaminho ? -rx : rx
        const finalRy = -ry // Desfaz a inversão do eixo Y

        return {
          ...pt,
          x: W1.x + finalRx,
          y: W1.y + finalRy
        }
      }
    }

    // 2. Inverso por 1 âncora + Escala + Giro da Bússola - MESMA transformação que o
    //    backend usa (desalinhar_ponto em processar_vistoria.py). Aplica SEMPRE que
    //    houver âncora A (ver comentário equivalente em alinharPonto acima).
    if (ancora1) {
      const rx = (pt.x - ancora1.x) / pathScale
      const ry = (pt.y - ancora1.y) / pathScale
      const theta = ((headingOffset + 180) * Math.PI) / 180

      const dx = rx * Math.cos(theta) + ry * Math.sin(theta)
      const dy = -rx * Math.sin(theta) + ry * Math.cos(theta)

      return {
        ...pt,
        x: espelharCaminho ? -dx : dx,
        y: -dy
      }
    }

    // 3. Sem nenhuma âncora (reverso da centralização padrão)
    if (waypoints.length > 0) {
      const sorted = [...waypoints].sort((a, b) => a.t - b.t)
      const W1 = sorted[0]
      const rx = (pt.x - 0.5) / pathScale
      const ry = (pt.y - 0.5) / pathScale
      const finalRx = espelharCaminho ? -rx : rx
      const finalRy = -ry // Desfaz a inversão do eixo Y
      return {
        ...pt,
        x: W1.x + finalRx,
        y: W1.y + finalRy
      }
    }

    return pt
  }, [waypoints, isImported, ancora1, ancora2, headingOffset, pathScale, espelharCaminho])

  const waypointsAlinhados = useMemo(() => {
    return waypoints.map(alinharPonto)
  }, [waypoints, alinharPonto])

  const posicaoAlinhada = useMemo(() => {
    return alinharPonto(posicao)
  }, [posicao, alinharPonto])

  // Sincroniza estado de play/pause do player com o React
  useEffect(() => {
    if (!player) return
    const onPlay = () => setTocando(true)
    const onPause = () => setTocando(false)
    player.on('play', onPlay)
    player.on('pause', onPause)
    return () => {
      player.off('play', onPlay)
      player.off('pause', onPause)
    }
  }, [player])

  // Carrega lista de visitas para sobreposição (exclui a visita atual)
  useEffect(() => {
    listarVisitas().then(vList => {
      setListaVisitas(vList.filter(v => v.id !== id))
    })
  }, [id])

  // Carrega dados da visita principal ou inicializa em modo demonstração
  useEffect(() => {
    if (id === 'demo') {
      setVisita({
        id: 'demo',
        pavimento: 'Demonstração Local (Offline)',
        hls_url: '', // começa sem vídeo
        planta_url: '', // começa sem planta
        heading_offset: 0,
        status: 'ready'
      })
      setWaypoints([])
      setHeadingOffset(0)
      setAncora1(null)
      setAncora2(null)
      return
    }

    getVisita(id).then(v => {
      if (!v) { navigate('/'); return }
      setVisita(v)
      setWaypoints(v.waypoints || [])
      setHeadingOffset(v.heading_offset || 0)
      setAncora1(v.ancora1 || null)
      setAncora2(v.ancora2 || null)
      setIsImported(v.is_imported || false)
      setPathScale(v.path_scale ?? 0.50)
      setEspelharCaminho(v.espelhar_caminho ?? true)
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

  // Funções de Controle do Player de Vídeo
  const togglePlay = useCallback(() => {
    if (!player) return
    if (player.paused()) {
      player.play()
    } else {
      player.pause()
    }
  }, [player])

  const frameAnterior = useCallback(() => {
    if (!player) return
    // Pula 0.5s para trás (simula voltar um frame/trecho no passeio)
    player.currentTime(Math.max(0, player.currentTime() - 0.5))
  }, [player])

  const proximoFrame = useCallback(() => {
    if (!player) return
    // Pula 0.5s para frente (simula avançar um frame/trecho)
    player.currentTime(Math.min(player.duration(), player.currentTime() + 0.5))
  }, [player])

  const mudarVelocidade = useCallback((v) => {
    if (!player) return
    player.playbackRate(v)
    setVelocidade(v)
  }, [player])

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
    // Converte a coordenada clicada na planta [0, 1] de volta para o sistema de escala bruto
    const ptBruto = desalinharPonto({ x: pendente.x, y: pendente.y })
    
    const novo = { 
      t: Math.round(tempoAtual), 
      x: ptBruto.x, 
      y: ptBruto.y, 
      label, 
      observacao 
    }
    setWaypoints(prev => [...prev, novo])
    setPendente(null)
    setModoAdicionar(false)
  }

  function removerWaypoint(index) {
    const sorted = [...waypoints].sort((a, b) => a.t - b.t)
    sorted.splice(index, 1)
    setWaypoints(sorted)
  }

  const atualizarPosicaoWaypoint = useCallback((index, newPtPlanta) => {
    // Converte a coordenada movida na planta [0, 1] de volta para o sistema de escala bruto
    const ptBruto = desalinharPonto(newPtPlanta)
    setWaypoints(prev => {
      const next = [...prev].sort((a, b) => a.t - b.t)
      if (next[index]) {
        next[index] = {
          ...next[index],
          x: ptBruto.x,
          y: ptBruto.y
        }
      }
      return next
    })
  }, [desalinharPonto])

  async function salvar() {
    if (id === 'demo') {
      mostrarToast('O modo demo é apenas para testes locais temporários (não salvos no Firebase).', 'erro')
      return
    }
    setSalvando(true)
    try {
      await atualizarVisita(id, {
        waypoints,
        heading_offset: headingOffset,
        ancora1,
        ancora2,
        path_scale: pathScale,
        espelhar_caminho: espelharCaminho,
      })
      mostrarToast('Alterações salvas com sucesso!')
    } catch (e) {
      mostrarToast('Erro ao salvar no banco', 'erro')
    } finally {
      setSalvando(false)
    }
  }

  function exportarTrajetoria() {
    if (!waypointsAlinhados || waypointsAlinhados.length === 0) {
      mostrarToast('Nenhum waypoint cadastrado para exportar.', 'erro')
      return
    }
    // Filtra para exportar apenas os dados limpos de trajetória
    const pureWaypoints = waypointsAlinhados.map(wp => ({
      t: wp.t,
      x: wp.x,
      y: wp.y,
      label: wp.label || '',
      observacao: wp.observacao || ''
    }))
    
    const dataStr = "data:text/json;charset=utf-8," + encodeURIComponent(JSON.stringify(pureWaypoints, null, 2))
    const downloadAnchor = document.createElement('a')
    downloadAnchor.setAttribute("href", dataStr)
    const fileName = `gabarito_trajetoria_${visita?.pavimento?.replace(/\s+/g, '_').toLowerCase() || 'vistoria'}.json`
    downloadAnchor.setAttribute("download", fileName)
    document.body.appendChild(downloadAnchor)
    downloadAnchor.click()
    downloadAnchor.remove()
    mostrarToast('JSON de trajetória exportado!')
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

  // Define as dimensões do mapa flutuante baseado no tamanho selecionado
  const mapaDimensões = {
    sm: 'w-[360px] h-[240px]',
    md: 'w-[520px] h-[340px]',
    lg: 'w-[700px] h-[450px]',
  }

  return (
    <div className="h-screen w-screen bg-black flex flex-col text-aco-200 overflow-hidden relative">
      
      {/* Topbar */}
      <header className="h-14 bg-concreto-900/85 backdrop-blur border-b border-concreto-700/60 flex items-center justify-between px-6 shrink-0 shadow-lg z-20">
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

      {/* Main View Area */}
      <main className="flex-1 w-full relative min-h-0 overflow-hidden z-10">
        
        {/* PLAYER 360° EM TELA CHEIA (Z-INDEX 0) */}
        <div className="absolute inset-0 w-full h-full z-0">
          <Player360
            hlsUrl={visita.hls_url}
            onReady={registrarPlayer}
            autoplay={false}
            waypoints={waypoints}
            posicao={posicao}
            headingOffset={headingOffset}
            lineOpacity={lineOpacity}
            lineThickness={lineThickness}
            espelharCaminho={espelharCaminho}
          />
        </div>

        {/* CONTROL BAR INTERATIVA NO RODAPÉ (Z-INDEX 20) */}
        <div className="absolute bottom-4 left-1/2 -translate-x-1/2 z-20 flex items-center gap-4 bg-concreto-900/90 backdrop-blur-md border border-concreto-700/80 rounded-xl px-4 py-2 shadow-2xl shrink-0">
          <button
            onClick={frameAnterior}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-aco-300 hover:text-sinal-400 hover:bg-concreto-800 transition-all font-mono font-bold text-sm"
            title="Voltar 0.5s (Frame anterior)"
          >
            ‹
          </button>
          <button
            onClick={togglePlay}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-aco-200 hover:text-sinal-400 hover:bg-concreto-800 transition-all text-xs"
            title={tocando ? 'Pausar' : 'Iniciar'}
          >
            {tocando ? '⏸' : '▶'}
          </button>
          <button
            onClick={proximoFrame}
            className="w-8 h-8 rounded-lg flex items-center justify-center text-aco-300 hover:text-sinal-400 hover:bg-concreto-800 transition-all font-mono font-bold text-sm"
            title="Avançar 0.5s (Próximo frame)"
          >
            ›
          </button>
          <div className="w-px h-5 bg-concreto-800" />
          <button
            onClick={() => setMostrarConfigPopup(p => !p)}
            className={`w-8 h-8 rounded-lg flex items-center justify-center transition-all ${
              mostrarConfigPopup ? 'text-sinal-400 bg-concreto-800' : 'text-aco-400 hover:text-aco-200 hover:bg-concreto-800'
            }`}
            title="Ajustes de Exibição"
          >
            ⚙️
          </button>
        </div>

        {/* POPUP FLUTUANTE DE CONFIGURAÇÕES (Z-INDEX 30) */}
        {mostrarConfigPopup && (
          <div className="absolute bottom-16 left-1/2 -translate-x-1/2 z-30 bg-concreto-900/95 backdrop-blur-md border border-concreto-700/80 rounded-xl p-4 w-[280px] shadow-2xl flex flex-col gap-4 text-xs font-sans">
            <div className="flex items-center justify-between border-b border-concreto-800 pb-1.5 shrink-0">
              <span className="font-mono text-[9px] text-aco-300 font-semibold uppercase tracking-wider">Ajustes da Passarela</span>
              <button
                onClick={() => setMostrarConfigPopup(false)}
                className="text-aco-400 hover:text-alerta text-[10px] font-mono"
              >
                Fechar
              </button>
            </div>

            {/* Playback Speed */}
            <div className="space-y-1.5">
              <span className="text-[11px] font-semibold text-aco-300 font-mono block">Velocidade de Reprodução</span>
              <div className="grid grid-cols-3 gap-1.5">
                {[0.5, 1.0, 1.5].map(v => (
                  <button
                    key={v}
                    onClick={() => mudarVelocidade(v)}
                    className={`py-1 rounded border font-mono text-[10px] transition-all ${
                      velocidade === v
                        ? 'bg-sinal-500 text-concreto-950 font-bold border-sinal-500'
                        : 'bg-concreto-800 border-concreto-700 text-aco-400 hover:text-aco-200'
                    }`}
                  >
                    {v}x
                  </button>
                ))}
              </div>
            </div>

            {/* Opacity */}
            <div className="space-y-1.5">
              <div className="flex justify-between font-mono text-[11px]">
                <span className="text-aco-300">Opacidade</span>
                <span className="text-sinal-400 font-semibold">{lineOpacity}%</span>
              </div>
              <input
                type="range"
                min="0"
                max="100"
                value={lineOpacity}
                onChange={e => setLineOpacity(parseInt(e.target.value))}
                className="w-full h-1 bg-concreto-800 rounded appearance-none cursor-pointer accent-sinal-500"
              />
            </div>

            {/* Thickness */}
            <div className="space-y-1.5">
              <div className="flex justify-between font-mono text-[11px]">
                <span className="text-aco-300">Espessura</span>
                <span className="text-sinal-400 font-semibold">{lineThickness}x</span>
              </div>
              <input
                type="range"
                min="1"
                max="25"
                value={lineThickness * 10}
                onChange={e => setLineThickness(parseInt(e.target.value) / 10)}
                className="w-full h-1 bg-concreto-800 rounded appearance-none cursor-pointer accent-sinal-500"
              />
            </div>

            {/* Footer Info */}
            <div className="border-t border-concreto-800 pt-2.5 text-[9px] font-mono text-aco-400 flex flex-col gap-0.5 leading-normal">
              <span>📅 Realizado em: {visita.data?.toDate?.()?.toLocaleDateString('pt-BR') || ''}</span>
              <span>👤 Operador: Pedro Furman</span>
            </div>
          </div>
        )}

        {/* BOTÃO FLUTUANTE PARA ABRIR MAPA MINIMIZADO */}
        {tamanhoMapa === 'minimized' && (
          <button
            onClick={() => setTamanhoMapa('md')}
            className="absolute bottom-6 left-[calc(50%-160px)] z-10 bg-sinal-500 hover:bg-sinal-400 text-concreto-950 font-mono font-bold text-xs px-5 py-3 rounded-full shadow-2xl transition-all active:scale-[0.97] flex items-center gap-2 border border-sinal-400"
          >
            🗺️ Abrir Planta Baixa
          </button>
        )}

        {/* MAPA PLANTA BAIXA FLUTUANTE (Z-INDEX 10) */}
        {tamanhoMapa !== 'minimized' && (
          <div className={`absolute bottom-[76px] left-1/2 -translate-x-1/2 z-10 bg-concreto-950/90 backdrop-blur-md border border-concreto-700/80 rounded-xl p-3.5 shadow-2xl flex flex-col gap-2.5 transition-all duration-300 ${mapaDimensões[tamanhoMapa]}`}>
            
            {/* Control Bar do Mapa */}
            <div className="flex items-center justify-between border-b border-concreto-800/80 pb-2 shrink-0">
              <span className="font-mono text-[9px] text-aco-300 font-semibold uppercase tracking-widest flex items-center gap-1.5">
                <span className="w-1.5 h-1.5 rounded-full bg-sinal-500 animate-ping inline-block" />
                Mapa de Navegação
              </span>
              
              <div className="flex items-center gap-1.5">
                <button
                  onClick={() => setTamanhoMapa('sm')}
                  className={`w-5 h-5 rounded text-[10px] font-mono flex items-center justify-center border transition-all ${
                    tamanhoMapa === 'sm' ? 'bg-sinal-500 text-concreto-950 border-sinal-500 font-bold' : 'border-concreto-750 text-aco-400 hover:text-aco-200'
                  }`}
                  title="Pequeno"
                >
                  P
                </button>
                <button
                  onClick={() => setTamanhoMapa('md')}
                  className={`w-5 h-5 rounded text-[10px] font-mono flex items-center justify-center border transition-all ${
                    tamanhoMapa === 'md' ? 'bg-sinal-500 text-concreto-950 border-sinal-500 font-bold' : 'border-concreto-750 text-aco-400 hover:text-aco-200'
                  }`}
                  title="Médio"
                >
                  M
                </button>
                <button
                  onClick={() => setTamanhoMapa('lg')}
                  className={`w-5 h-5 rounded text-[10px] font-mono flex items-center justify-center border transition-all ${
                    tamanhoMapa === 'lg' ? 'bg-sinal-500 text-concreto-950 border-sinal-500 font-bold' : 'border-concreto-750 text-aco-400 hover:text-aco-200'
                  }`}
                  title="Grande"
                >
                  G
                </button>
                <div className="w-px h-3 bg-concreto-800" />
                <button
                  onClick={() => setTamanhoMapa('minimized')}
                  className="w-5 h-5 rounded text-[10px] flex items-center justify-center border border-concreto-750 text-aco-400 hover:text-alerta transition-all"
                  title="Minimizar"
                >
                  ✕
                </button>
              </div>
            </div>

            {/* Planta Canvas */}
            <div className="flex-1 min-h-0 relative rounded-lg overflow-hidden border border-concreto-800">
              <PlantaViewer
                plantaUrl={visita.planta_url}
                waypoints={waypointsAlinhados}
                posicao={posicaoAlinhada}
                waypointAtivo={waypointAtivo}
                onClickCoordenada={handleClickCoordenada}
                onClickWaypoint={pularParaWaypoint}
                player={player}
                headingOffset={headingOffset}
                modoCalibrarAncoras={modoCalibrarAncoras}
                ancora1={ancora1}
                ancora2={ancora2}
                visitaSobreposta={visitaSobreposta}
                espelharCaminho={espelharCaminho}
                onUpdateWaypointPosition={atualizarPosicaoWaypoint}
              />
            </div>
          </div>
        )}

        {/* BOTÃO FLUTUANTE PARA CONFIGURAÇÕES / DRAWER */}
        {!menuAberto && (
          <button
            onClick={() => setMenuAberto(true)}
            className="absolute top-4 right-4 z-10 bg-concreto-900/90 backdrop-blur border border-concreto-700/80 hover:border-sinal-500/50 text-aco-200 hover:text-sinal-400 px-4 py-2.5 rounded-lg font-mono text-xs shadow-xl transition-all active:scale-[0.97]"
          >
            ⚙️ Painel de Controle
          </button>
        )}

        {/* GAVETA RETRÁTIL DE CONFIGURAÇÃO & EDITOR (DRAWER Z-INDEX 15) */}
        {menuAberto && (
          <div className="absolute top-0 right-0 h-full w-[310px] bg-concreto-950/95 backdrop-blur-md border-l border-concreto-700/80 shadow-2xl flex flex-col p-4 gap-4 z-15 transition-transform duration-300 translate-x-0">
            
            {/* Header do Drawer */}
            <div className="flex items-center justify-between border-b border-concreto-800/80 pb-2.5">
              <span className="font-sans font-bold text-xs uppercase tracking-wider text-aco-100">Configurações</span>
              <button
                onClick={() => setMenuAberto(false)}
                className="text-aco-400 hover:text-alerta text-xs px-2.5 py-1 font-mono hover:underline"
              >
                Fechar ✕
              </button>
            </div>

            {/* Banner de status de processamento automático */}
            {visita.status === 'processado' && (
              <div className="flex items-center gap-2 bg-green-500/20 border border-green-500/40 rounded-lg px-3 py-2 mb-3">
                <span className="text-green-400 text-lg">✓</span>
                <div>
                  <p className="text-green-300 text-xs font-semibold font-mono">Trajetória Processada</p>
                  <p className="text-green-400/70 text-[10px] font-mono">Caminho gerado automaticamente por Map Matching</p>
                </div>
              </div>
            )}

            {/* Teste com Vídeo e Planta locais */}
            <div className="bg-concreto-900/55 border border-concreto-800/70 rounded-lg p-3 flex flex-col gap-3 shrink-0">
              <div>
                <span className="text-[11px] font-semibold text-aco-300 font-mono block mb-1">Vídeo MP4 Local (Teste)</span>
                <div className="relative border border-dashed border-concreto-700 hover:border-sinal-500/50 rounded bg-concreto-800/20 p-2 text-center cursor-pointer transition-all">
                  <input
                    type="file"
                    accept="video/mp4"
                    onChange={e => {
                      const file = e.target.files[0]
                      if (file) {
                        const localUrl = URL.createObjectURL(file)
                        setVisita(prev => ({ ...prev, hls_url: localUrl }))
                        mostrarToast('Vídeo local carregado no player!')
                      }
                    }}
                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                  />
                  <span className="text-[10px] text-aco-400 font-mono">Selecionar MP4 Local</span>
                </div>
              </div>

              <div>
                <span className="text-[11px] font-semibold text-aco-300 font-mono block mb-1">Planta Baixa Local (PDF ou Imagem)</span>
                <div className="relative border border-dashed border-concreto-700 hover:border-sinal-500/50 rounded bg-concreto-800/20 p-2 text-center cursor-pointer transition-all">
                  <input
                    type="file"
                    accept="application/pdf, image/*"
                    onChange={e => {
                      const file = e.target.files[0]
                      if (file) {
                        const localUrl = URL.createObjectURL(file)
                        setVisita(prev => ({ ...prev, planta_url: localUrl }))
                        mostrarToast('Planta baixa local carregada!')
                      }
                    }}
                    className="absolute inset-0 w-full h-full opacity-0 cursor-pointer"
                  />
                  <span className="text-[10px] text-aco-400 font-mono">Selecionar Planta Local (PDF ou Imagem)</span>
                </div>
              </div>
              
              <p className="text-[9px] text-aco-400 leading-normal font-mono">
                Permite testar a caminhada e a passarela 3D sem precisar de banco de dados ou fazer upload.
              </p>
            </div>

            {/* Slider de Calibração da Bússola */}
            <div className={`bg-concreto-900/55 border border-concreto-800/70 rounded-lg p-3 flex flex-col gap-2 shrink-0 ${ancora1 && ancora2 ? 'opacity-40 pointer-events-none' : ''}`}>
              <div className="flex justify-between items-center text-xs font-mono">
                <span className="text-aco-300 font-medium text-[11px]">Bússola (Alinhamento Norte)</span>
                <span className="text-sinal-400 font-bold bg-sinal-500/10 px-2 py-0.5 rounded text-[10px] border border-sinal-500/10">{headingOffset}°</span>
              </div>
              <input
                type="range"
                min="-180"
                max="180"
                disabled={!!(ancora1 && ancora2)}
                value={headingOffset}
                onChange={e => setHeadingOffset(parseInt(e.target.value))}
                className="w-full h-1 bg-concreto-800 rounded-lg appearance-none cursor-pointer accent-sinal-500 border border-concreto-700/40"
              />
              {ancora1 && ancora2 ? (
                <p className="text-[8px] text-sinal-400 font-mono">
                  Giro calculado automaticamente pelas Âncoras A e B.
                </p>
              ) : (
                <p className="text-[9px] text-aco-400 leading-normal font-mono">
                  Alinha o cone azul de visão no mapa à perspectiva real da câmera.
                </p>
              )}
            </div>

            {/* Slider de Tamanho da Trajetória (apenas se for trajetória importada) */}
            {isImported && (
              <>
                <div className={`bg-concreto-900/55 border border-concreto-800/70 rounded-lg p-3 flex flex-col gap-2 shrink-0 ${ancora1 && ancora2 ? 'opacity-40 pointer-events-none' : ''}`}>
                  <div className="flex justify-between items-center text-xs font-mono">
                    <span className="text-aco-300 font-medium text-[11px]">Tamanho do Caminho (Escala)</span>
                    <span className="text-sinal-400 font-bold bg-sinal-500/10 px-2 py-0.5 rounded text-[10px] border border-sinal-500/10">{Math.round(pathScale * 100)}%</span>
                  </div>
                  <input
                    type="range"
                    min="1"
                    max="100"
                    step="1"
                    disabled={!!(ancora1 && ancora2)}
                    value={Math.round(pathScale * 100)}
                    onChange={e => setPathScale(parseFloat(e.target.value) / 100)}
                    className="w-full h-1 bg-concreto-800 rounded-lg appearance-none cursor-pointer accent-sinal-500 border border-concreto-700/40"
                  />
                  {ancora1 && ancora2 ? (
                    <p className="text-[8px] text-sinal-400 font-mono">
                      Escala calculada automaticamente pelas Âncoras A e B.
                    </p>
                  ) : (
                    <p className="text-[9px] text-aco-400 leading-normal font-mono">
                      Aumenta ou diminui a escala do trajeto para caber na planta baixa.
                    </p>
                  )}
                </div>

                {/* Toggle de Espelhamento Horizontal */}
                <div className="bg-concreto-900/55 border border-concreto-800/70 rounded-lg p-3 flex items-center justify-between shrink-0 font-mono text-xs">
                  <span className="text-aco-300 text-[11px]">Espelhar Trajetória (Inverter E/D)</span>
                  <label className="relative inline-flex items-center cursor-pointer">
                    <input
                      type="checkbox"
                      checked={espelharCaminho}
                      onChange={e => setEspelharCaminho(e.target.checked)}
                      className="sr-only peer"
                    />
                    <div className="w-9 h-5 bg-concreto-800 peer-focus:outline-none rounded-full peer peer-checked:after:translate-x-full peer-checked:after:border-white after:content-[''] after:absolute after:top-[2px] after:left-[2px] after:bg-aco-200 after:border-concreto-700 after:border after:rounded-full after:h-4 after:w-4 after:transition-all peer-checked:bg-sinal-500 peer-checked:after:bg-concreto-950"></div>
                  </label>
                </div>
              </>
            )}

            {/* Editor de Waypoints */}
            <div className="flex-1 min-h-0 flex flex-col">
              <WaypointEditor
                waypoints={waypointsAlinhados}
                tempoAtual={tempoAtual}
                duracao={duracao}
                modoAdicionar={modoAdicionar}
                onToggleModo={() => { setModoAdicionar(v => !v); setPendente(null) }}
                onRemover={removerWaypoint}
                onSalvar={salvar}
                onExportar={exportarTrajetoria}
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
        )}

      </main>
    </div>
  )
}
