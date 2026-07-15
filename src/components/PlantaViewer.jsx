// src/components/PlantaViewer.jsx
import { useRef, useEffect, useCallback, useState } from 'react'
import * as THREE from 'three'
import * as pdfjs from 'pdfjs-dist'

// Configura o worker de forma local compatível com Vite
import pdfjsWorker from 'pdfjs-dist/build/pdf.worker.mjs?worker'
pdfjs.GlobalWorkerOptions.workerPort = new pdfjsWorker()

/**
 * Renderiza a planta PNG num canvas preservando a proporcao original do arquivo (sem achatar)
 * e adiciona controles interativos de Pan & Zoom, além de pins arrastáveis para ajuste fino.
 */
export default function PlantaViewer({
  plantaUrl,
  waypoints = [],
  frames = [], // marcadores individuais de FOTO (um por quadro do manifest.json, com
               // x/y/t já alinhados via alinharPonto em Visita.jsx) - pedido do Pedro
               // em 2026-07-15: ver onde cada foto fica na planta pra clicar de forma
               // mais assertiva, em vez de só a linha tracejada da trajetória bruta
  posicao,
  waypointAtivo,
  onClickCoordenada,
  onClickWaypoint,
  onUpdateWaypointPosition, // Callback para atualizar a posição de um waypoint arrastado
  player,
  headingOffset = 0,
  modoCalibrarAncoras = null, // null | 'ancora1' | 'ancora2'
  ancora1,
  ancora2,
  visitaSobreposta = null, // { planta_url, ancora1, ancora2 }
  espelharCaminho = false,
  coneFrameOffset = 0, // graus - ajuste manual da direcao do cone relativo ao frame/foto,
                        // independente do headingOffset (que orienta o mapa todo)
}) {
  const canvasRef = useRef(null)
  const imgRef = useRef(null)
  const imgSobrepostaRef = useRef(null)
  const animFrameRef = useRef(null)

  // Estados de Pan e Zoom
  const [zoom, setZoom] = useState(1.0)
  const [pan, setPan] = useState({ x: 0, y: 0 })
  const [errorMsg, setErrorMsg] = useState(null)
  
  // Dragging de mapa
  const isDraggingRef = useRef(false)
  const dragStartRef = useRef({ x: 0, y: 0 })
  const panStartRef = useRef({ x: 0, y: 0 })
  const totalDragDistRef = useRef(0)

  // Dragging de waypoint pin
  const isDraggingPinRef = useRef(false)
  const draggedPinRef = useRef(null)

  // Função auxiliar para carregar imagem (suporta PDF e Imagem convencional)
  const carregarImagemPlanta = useCallback((url, callback) => {
    if (!url) return;
    setErrorMsg(null);
    
    const isPdf = url.toLowerCase().includes('.pdf') || url.startsWith('data:application/pdf');
    
    if (isPdf) {
      const renderPdf = async () => {
        try {
          // Busca o PDF como ArrayBuffer primeiro (contorna CORS e auth do Firebase)
          const response = await fetch(url);
          if (!response.ok) {
            throw new Error(`Falha ao buscar PDF (HTTP ${response.status} ${response.statusText})`);
          }
          const arrayBuffer = await response.arrayBuffer();

          const loadingTask = pdfjs.getDocument({ data: arrayBuffer });
          const pdf = await loadingTask.promise;
          const page = await pdf.getPage(1);
          
          // Renderiza a prancha a 2.0x de escala para manter ótima definição de zoom
          const viewport = page.getViewport({ scale: 2.0 });
          
          const canvas = document.createElement('canvas');
          const context = canvas.getContext('2d');
          canvas.width = viewport.width;
          canvas.height = viewport.height;
          
          const renderContext = {
            canvasContext: context,
            viewport: viewport
          };
          
          await page.render(renderContext).promise;
          const dataUrl = canvas.toDataURL('image/png');
          
          const img = new Image();
          img.src = dataUrl;
          img.onload = () => callback(img);
          img.onerror = () => setErrorMsg("Falha ao instanciar imagem do PDF renderizado.");
        } catch (error) {
          console.error("Erro ao carregar/renderizar planta PDF:", error);
          setErrorMsg(error.message || error.toString());
        }
      };
      renderPdf();
    } else {
      const img = new Image();
      img.src = url;
      img.onload = () => callback(img);
      img.onerror = () => setErrorMsg("Erro ao carregar arquivo de imagem.");
    }
  }, []);

  // Carrega imagem principal
  useEffect(() => {
    if (!plantaUrl) return
    carregarImagemPlanta(plantaUrl, (img) => {
      imgRef.current = img
      // Reseta pan e zoom ao mudar de planta
      setZoom(1.0)
      setPan({ x: 0, y: 0 })
    })
  }, [plantaUrl, carregarImagemPlanta])

  // Carrega imagem de sobreposição
  useEffect(() => {
    if (!visitaSobreposta?.planta_url) {
      imgSobrepostaRef.current = null
      return
    }
    carregarImagemPlanta(visitaSobreposta.planta_url, (img) => {
      imgSobrepostaRef.current = img
    })
  }, [visitaSobreposta, carregarImagemPlanta])

  const getCameraYaw = useCallback(() => {
    if (!player) return null
    try {
      const vr = player.vr?.()
      const camera = vr?.camera || vr?.camera_
      if (camera) {
        // Obtém o vetor unitário absoluto para onde a câmera está apontando no mundo 3D
        const dir = new THREE.Vector3()
        camera.getWorldDirection(dir)
        
        // Em Three.js, Y é para cima, X é para a direita e Z é para trás (vetor de visão padrão é -Z).
        // Calculamos o yaw no plano XZ em relação ao eixo Z negativo (frente)
        return Math.atan2(dir.x, -dir.z)
      }
    } catch (e) {
      // Falha silenciosa caso o plugin ainda não esteja inicializado
    }
    return null
  }, [player])

  // Projetar coordenada normalizada [0, 1] no canvas baseado no Pan & Zoom atuais
  const toCanvasPixels = useCallback((x, y) => {
    const canvas = canvasRef.current
    if (!canvas || !imgRef.current) return { cx: 0, cy: 0 }
    const W = canvas.width
    const H = canvas.height
    const scaleToFit = Math.min(W / imgRef.current.width, H / imgRef.current.height)
    const baseScale = scaleToFit * zoom
    const startX = W / 2 + pan.x - (imgRef.current.width * baseScale) / 2
    const startY = H / 2 + pan.y - (imgRef.current.height * baseScale) / 2
    return {
      cx: startX + x * imgRef.current.width * baseScale,
      cy: startY + y * imgRef.current.height * baseScale
    }
  }, [zoom, pan])

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const W = canvas.width
    const H = canvas.height

    ctx.clearRect(0, 0, W, H)

    // 1. Desenha Planta Principal (Preservando Aspect Ratio e aplicando Zoom/Pan)
    if (!imgRef.current) {
      ctx.fillStyle = '#1a1c20'
      ctx.fillRect(0, 0, W, H)
      ctx.fillStyle = errorMsg ? '#ef4444' : '#3c424d'
      ctx.font = '13px JetBrains Mono'
      ctx.textAlign = 'center'
      if (errorMsg) {
        ctx.fillText('Erro ao carregar PDF:', W / 2, H / 2 - 10)
        ctx.fillText(errorMsg.slice(0, 80), W / 2, H / 2 + 15)
      } else {
        ctx.fillText('Carregando planta...', W / 2, H / 2)
      }
      return
    }

    const img = imgRef.current
    const scaleToFit = Math.min(W / img.width, H / img.height)
    const baseScale = scaleToFit * zoom
    const startX = W / 2 + pan.x - (img.width * baseScale) / 2
    const startY = H / 2 + pan.y - (img.height * baseScale) / 2

    ctx.drawImage(img, startX, startY, img.width * baseScale, img.height * baseScale)

    // 2. Desenha Planta Sobreposta Alinhada Geometricamente (Procrustes 2D)
    if (imgSobrepostaRef.current && visitaSobreposta?.ancora1 && visitaSobreposta?.ancora2 && ancora1 && ancora2) {
      const { cx: ax1, cy: ay1 } = toCanvasPixels(ancora1.x, ancora1.y)
      const { cx: ax2, cy: ay2 } = toCanvasPixels(ancora2.x, ancora2.y)

      const oW = imgSobrepostaRef.current.width * baseScale
      const oH = imgSobrepostaRef.current.height * baseScale

      const bx1 = visitaSobreposta.ancora1.x * oW
      const by1 = visitaSobreposta.ancora1.y * oH
      const bx2 = visitaSobreposta.ancora2.x * oW
      const by2 = visitaSobreposta.ancora2.y * oH

      const dAx = ax2 - ax1
      const dAy = ay2 - ay1
      const dBx = bx2 - bx1
      const dBy = by2 - by1

      const distA = Math.sqrt(dAx * dAx + dAy * dAy)
      const distB = Math.sqrt(dBx * dBx + dBy * dBy)

      if (distA > 0 && distB > 0) {
        const scale = distA / distB
        const angleA = Math.atan2(dAy, dAx)
        const angleB = Math.atan2(dBy, dBx)
        const rotation = angleA - angleB

        ctx.save()
        ctx.translate(ax1, ay1)
        ctx.rotate(rotation)
        ctx.scale(scale, scale)
        ctx.translate(-bx1, -by1)

        ctx.globalAlpha = 0.40 // 40% de opacidade
        ctx.drawImage(imgSobrepostaRef.current, 0, 0, oW, oH)
        ctx.restore()
      }
    }

    const sorted = [...waypoints].sort((a, b) => a.t - b.t)

    // 3. Desenha Linha de trajetoria
    if (sorted.length > 1) {
      ctx.beginPath()
      ctx.strokeStyle = 'rgba(245, 158, 11, 0.4)'
      ctx.lineWidth = 2.5
      ctx.setLineDash([6, 4])
      sorted.forEach((wp, i) => {
        const { cx, cy } = toCanvasPixels(wp.x, wp.y)
        if (i === 0) ctx.moveTo(cx, cy)
        else ctx.lineTo(cx, cy)
      })
      ctx.stroke()
      ctx.setLineDash([])
    }

    // 3.5 Desenha marcadores individuais de FOTO (um por quadro do manifest.json) -
    // azul mais claro que os pins de waypoint/ancora, só um ponto pequeno indicando
    // "aqui existe uma foto navegável". Ajuda a clicar de forma mais assertiva no
    // trajeto em vez de tentar acertar um ponto qualquer da linha tracejada - ver
    // hit-test correspondente em processClick abaixo.
    if (frames.length > 0) {
      ctx.fillStyle = 'rgba(147, 197, 253, 0.85)'
      frames.forEach((fr) => {
        const { cx, cy } = toCanvasPixels(fr.x, fr.y)
        ctx.beginPath()
        ctx.arc(cx, cy, 2.4, 0, Math.PI * 2)
        ctx.fill()
      })
    }

    // 4. Desenha Cone de Visao (FOV) por rotação vetorial direta
    // Não usa Math.atan2 nem ctx.arc, evitando inversão de esquerda/direita.
    // O triângulo é construído rotacionando ±30° o vetor de visão (rx, ry).
    const yaw = getCameraYaw()
    if (posicao && yaw !== null) {
      const { cx, cy } = toCanvasPixels(posicao.x, posicao.y)

      // Projeta o vetor de visão usando a mesma transformação da trajetória
      // NOTA: para o cone FOV usamos o sinal OPOSTO do espelhamento porque o
      // yaw da câmera já está no espaço absoluto do mundo (não precisa ser espelhado junto com a trajetória)
      const theta = ((headingOffset + 180) * Math.PI) / 180
      // coneFrameOffset: ajuste manual adicional (independente da bussola/headingOffset,
      // que gira o mapa inteiro) pra corrigir residuos de desalinhamento entre o cone e
      // o frame/foto atual - ex.: se cada foto extraida nao guarda exatamente a mesma
      // referencia de "frente" que o PanoramaViewer assume.
      const yawAjustado = yaw + (coneFrameOffset * Math.PI) / 180
      const dx = espelharCaminho ? Math.sin(yawAjustado) : -Math.sin(yawAjustado)
      const dy = -Math.cos(yawAjustado)

      // Vetor de visão rotacionado pela bússola (no espaço do canvas)
      const rx = dx * Math.cos(theta) - dy * Math.sin(theta)
      const ry = dx * Math.sin(theta) + dy * Math.cos(theta)

      // Raio e abertura do cone (60° total = ±30°)
      const radius = 60 * Math.max(0.5, Math.min(zoom, 3))
      const halfAngle = (30 * Math.PI) / 180
      const cosH = Math.cos(halfAngle)
      const sinH = Math.sin(halfAngle)

      // Rotaciona (rx, ry) por -30° → borda esquerda
      const lx = rx * cosH + ry * sinH
      const ly = -rx * sinH + ry * cosH

      // Rotaciona (rx, ry) por +30° → borda direita
      const rrx = rx * cosH - ry * sinH
      const rry = rx * sinH + ry * cosH

      // Pontos finais das bordas do triângulo no canvas
      const tipLx = cx + lx * radius
      const tipLy = cy + ly * radius
      const tipRx = cx + rrx * radius
      const tipRy = cy + rry * radius

      // Preenchimento com gradiente radial
      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius)
      grad.addColorStop(0, 'rgba(59, 130, 246, 0.5)')
      grad.addColorStop(0.3, 'rgba(59, 130, 246, 0.25)')
      grad.addColorStop(1, 'rgba(59, 130, 246, 0)')

      ctx.beginPath()
      ctx.moveTo(cx, cy)
      ctx.lineTo(tipLx, tipLy)
      ctx.lineTo(tipRx, tipRy)
      ctx.closePath()
      ctx.fillStyle = grad
      ctx.fill()

      // Bordas do triângulo
      ctx.beginPath()
      ctx.moveTo(cx, cy)
      ctx.lineTo(tipLx, tipLy)
      ctx.moveTo(cx, cy)
      ctx.lineTo(tipRx, tipRy)
      ctx.strokeStyle = 'rgba(59, 130, 246, 0.5)'
      ctx.lineWidth = 1.5
      ctx.stroke()
    }

    // 5. Desenha Pins dos waypoints
    sorted.forEach((wp) => {
      const { cx, cy } = toCanvasPixels(wp.x, wp.y)
      const isAtivo = waypointAtivo?.t === wp.t

      ctx.beginPath()
      ctx.arc(cx, cy, isAtivo ? 10 : 7, 0, Math.PI * 2)
      ctx.fillStyle = isAtivo ? 'rgba(245,158,11,0.25)' : 'rgba(255,255,255,0.1)'
      ctx.fill()

      ctx.beginPath()
      ctx.arc(cx, cy, isAtivo ? 5 : 4, 0, Math.PI * 2)
      ctx.fillStyle = isAtivo ? '#f59e0b' : '#8a9ab0'
      ctx.fill()

      if (isAtivo && wp.label) {
        ctx.fillStyle = '#f59e0b'
        ctx.font = '600 11px Inter'
        ctx.textAlign = 'center'
        ctx.fillText(wp.label, cx, cy - 14)
      }
    })

    // 6. Desenha Ponto de posicao atual (interpolada)
    if (posicao) {
      const { cx, cy } = toCanvasPixels(posicao.x, posicao.y)
      const t = Date.now() / 600
      const pulse = 11 + Math.sin(t) * 3

      ctx.beginPath()
      ctx.arc(cx, cy, pulse, 0, Math.PI * 2)
      ctx.fillStyle = 'rgba(34, 197, 94, 0.2)'
      ctx.fill()

      ctx.beginPath()
      ctx.arc(cx, cy, 6, 0, Math.PI * 2)
      ctx.fillStyle = '#22c55e'
      ctx.fill()

      ctx.beginPath()
      ctx.arc(cx, cy, 2.5, 0, Math.PI * 2)
      ctx.fillStyle = '#ffffff'
      ctx.fill()
    }

    // 7. Desenha Ancoras
    if (ancora1) {
      const { cx, cy } = toCanvasPixels(ancora1.x, ancora1.y)
      ctx.beginPath()
      ctx.arc(cx, cy, 7, 0, Math.PI * 2)
      ctx.fillStyle = '#3b82f6'
      ctx.fill()
      ctx.strokeStyle = '#ffffff'
      ctx.lineWidth = 1.5
      ctx.stroke()
      ctx.fillStyle = '#ffffff'
      ctx.font = 'bold 9px monospace'
      ctx.textAlign = 'center'
      ctx.fillText('A', cx, cy + 3)
    }
    if (ancora2) {
      const { cx, cy } = toCanvasPixels(ancora2.x, ancora2.y)
      ctx.beginPath()
      ctx.arc(cx, cy, 7, 0, Math.PI * 2)
      ctx.fillStyle = '#ef4444'
      ctx.fill()
      ctx.strokeStyle = '#ffffff'
      ctx.lineWidth = 1.5
      ctx.stroke()
      ctx.fillStyle = '#ffffff'
      ctx.font = 'bold 9px monospace'
      ctx.textAlign = 'center'
      ctx.fillText('B', cx, cy + 3)
    }
  }, [waypoints, frames, posicao, waypointAtivo, getCameraYaw, headingOffset, ancora1, ancora2, visitaSobreposta, zoom, pan, espelharCaminho, toCanvasPixels, coneFrameOffset])

  // Reanima continuamente
  useEffect(() => {
    const loop = () => {
      draw()
      animFrameRef.current = requestAnimationFrame(loop)
    }
    animFrameRef.current = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(animFrameRef.current)
  }, [draw])

  // Eventos de Pan & Zoom e Dragging de Waypoints
  const handleMouseDown = useCallback((e) => {
    if (e.button !== 0) return // Apenas botao esquerdo do mouse
    
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const mouseX = ((e.clientX - rect.left) / rect.width) * canvas.width
    const mouseY = ((e.clientY - rect.top) / rect.height) * canvas.height

    // 1. Verifica se clicou muito proximo de um waypoint para arrasta-lo
    const sorted = [...waypoints].sort((a, b) => a.t - b.t)
    const PIN_HIT_RADIUS = 15 // tolerancia em pixels
    let clickedPinIndex = -1

    for (let i = 0; i < sorted.length; i++) {
      const { cx, cy } = toCanvasPixels(sorted[i].x, sorted[i].y)
      const dist = Math.sqrt((cx - mouseX) ** 2 + (cy - mouseY) ** 2)
      if (dist < PIN_HIT_RADIUS) {
        clickedPinIndex = i
        break
      }
    }

    if (clickedPinIndex !== -1 && onUpdateWaypointPosition) {
      draggedPinRef.current = clickedPinIndex
      isDraggingPinRef.current = true
    } else {
      // 2. Se nao clicou num pin, arrasta o mapa (Pan)
      isDraggingRef.current = true
      dragStartRef.current = { x: e.clientX, y: e.clientY }
      panStartRef.current = { ...pan }
    }
    totalDragDistRef.current = 0
  }, [waypoints, pan, toCanvasPixels, onUpdateWaypointPosition])

  const handleMouseMove = useCallback((e) => {
    const canvas = canvasRef.current
    if (!canvas) return

    // Caso 1: Arrastando um waypoint
    if (isDraggingPinRef.current && draggedPinRef.current !== null && onUpdateWaypointPosition) {
      const rect = canvas.getBoundingClientRect()
      const cx = ((e.clientX - rect.left) / rect.width) * canvas.width
      const cy = ((e.clientY - rect.top) / rect.height) * canvas.height

      const img = imgRef.current
      if (img) {
        const scaleToFit = Math.min(canvas.width / img.width, canvas.height / img.height)
        const baseScale = scaleToFit * zoom
        const startX = canvas.width / 2 + pan.x - (img.width * baseScale) / 2
        const startY = canvas.height / 2 + pan.y - (img.height * baseScale) / 2

        // Calcula a nova coordenada normalizada [0, 1] da planta
        const px = (cx - startX) / (img.width * baseScale)
        const py = (cy - startY) / (img.height * baseScale)

        // Limita a movimentacao dentro das bordas da planta baixa [0, 1]
        const clampedX = Math.max(0, Math.min(px, 1.0))
        const clampedY = Math.max(0, Math.min(py, 1.0))

        onUpdateWaypointPosition(draggedPinRef.current, { x: clampedX, y: clampedY })
      }
      totalDragDistRef.current = 10 // garante que nao contara como clique ao soltar
      return
    }

    // Caso 2: Arrastando a camera do visualizador (Pan)
    if (isDraggingRef.current) {
      const dx = e.clientX - dragStartRef.current.x
      const dy = e.clientY - dragStartRef.current.y
      totalDragDistRef.current = Math.sqrt(dx * dx + dy * dy)
      setPan({
        x: panStartRef.current.x + dx,
        y: panStartRef.current.y + dy
      })
    }
  }, [zoom, pan, onUpdateWaypointPosition])

  const handleWheel = useCallback((e) => {
    e.preventDefault()
    const zoomFactor = 1.15
    let newZoom = zoom
    
    // Normaliza a rolagem para torná-la muito mais suave (mouses e trackpads)
    const delta = -e.deltaY * 0.0010
    const factor = Math.exp(delta)
    newZoom = Math.max(0.4, Math.min(zoom * factor, 12.0))

    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const mouseX = ((e.clientX - rect.left) / rect.width) * canvas.width
    const mouseY = ((e.clientY - rect.top) / rect.height) * canvas.height

    const dx = mouseX - canvas.width / 2 - pan.x
    const dy = mouseY - canvas.height / 2 - pan.y
    const ratio = newZoom / zoom

    setPan({
      x: mouseX - canvas.width / 2 - dx * ratio,
      y: mouseY - canvas.height / 2 - dy * ratio
    })
    setZoom(newZoom)
  }, [zoom, pan])

  const processClick = useCallback((e) => {
    const canvas = canvasRef.current
    if (!canvas || !imgRef.current) return
    const rect = canvas.getBoundingClientRect()
    
    const cx = ((e.clientX - rect.left) / rect.width) * canvas.width
    const cy = ((e.clientY - rect.top) / rect.height) * canvas.height

    const img = imgRef.current
    const scaleToFit = Math.min(canvas.width / img.width, canvas.height / img.height)
    const baseScale = scaleToFit * zoom

    const startX = canvas.width / 2 + pan.x - (img.width * baseScale) / 2
    const startY = canvas.height / 2 + pan.y - (img.height * baseScale) / 2

    const x = (cx - startX) / (img.width * baseScale)
    const y = (cy - startY) / (img.height * baseScale)

    if (modoCalibrarAncoras) {
      if (onClickCoordenada) onClickCoordenada(x, y)
      return
    }

    const THRESH = 0.030 / zoom
    const clicked = waypoints.find(
      wp => Math.sqrt((wp.x - x) ** 2 + (wp.y - y) ** 2) < THRESH
    )

    if (clicked && onClickWaypoint) {
      onClickWaypoint(clicked)
      return
    }

    // Clique perto de uma FOTO especifica (marcador azul-claro, ver desenho acima):
    // pula direto pro tempo exato daquela foto, em vez de cair na interpolação por
    // segmento mais abaixo (que pode acertar um ponto qualquer entre duas fotos).
    if (frames.length > 0 && player) {
      const FRAME_THRESH = 0.018 / zoom
      let melhorFrame = null
      let melhorDist = Infinity
      for (const fr of frames) {
        const d = Math.sqrt((fr.x - x) ** 2 + (fr.y - y) ** 2)
        if (d < melhorDist) { melhorDist = d; melhorFrame = fr }
      }
      if (melhorFrame && melhorDist < FRAME_THRESH) {
        player.currentTime(melhorFrame.t)
        return
      }
    }

    if (waypoints.length > 1 && player) {
      const sorted = [...waypoints].sort((a, b) => a.t - b.t)
      let minDistance = Infinity
      let bestTime = null

      for (let i = 0; i < sorted.length - 1; i++) {
        const A = sorted[i]
        const B = sorted[i + 1]

        const abX = B.x - A.x
        const abY = B.y - A.y
        const abLen2 = abX * abX + abY * abY

        if (abLen2 === 0) continue

        const apX = x - A.x
        const apY = y - A.y
        const r = Math.max(0, Math.min(1, (apX * abX + apY * abY) / abLen2))

        const cX = A.x + r * abX
        const cY = A.y + r * abY

        const dist = Math.sqrt((x - cX) ** 2 + (y - cY) ** 2)

        if (dist < minDistance) {
          minDistance = dist
          bestTime = A.t + r * (B.t - A.t)
        }
      }

      const PATH_THRESH = 0.022 / zoom
      if (minDistance < PATH_THRESH && bestTime !== null) {
        player.currentTime(bestTime)
        return
      }
    }

    if (onClickCoordenada) {
      onClickCoordenada(x, y)
    }
  }, [waypoints, frames, onClickCoordenada, onClickWaypoint, modoCalibrarAncoras, player, zoom, pan])

  const handleMouseUp = useCallback((e) => {
    isDraggingRef.current = false
    isDraggingPinRef.current = false
    draggedPinRef.current = null
    if (totalDragDistRef.current < 6) {
      processClick(e)
    }
  }, [processClick])

  return (
    <div className="relative w-full h-full rounded-lg overflow-hidden bg-concreto-900 border border-concreto-700 select-none">
      
      {/* Botao de Centralizacao / Reset de Zoom */}
      <div className="absolute top-3 left-3 flex gap-1.5 z-10">
        <button
          onClick={() => { setZoom(1.0); setPan({ x: 0, y: 0 }) }}
          className="bg-concreto-950/85 hover:bg-concreto-800 backdrop-blur border border-concreto-700/60 text-aco-200 hover:text-sinal-400 px-2.5 py-1.5 rounded text-[10px] font-mono transition-all active:scale-95 shadow-md"
          title="Resetar Zoom e Centralizar"
        >
          🏠 Centralizar
        </button>
      </div>

      <canvas
        ref={canvasRef}
        width={900}
        height={600}
        className="w-full h-full cursor-grab active:cursor-grabbing"
        onMouseDown={handleMouseDown}
        onMouseMove={handleMouseMove}
        onMouseUp={handleMouseUp}
        onMouseLeave={() => { 
          isDraggingRef.current = false; 
          isDraggingPinRef.current = false;
        }}
        onWheel={handleWheel}
        style={{ imageRendering: 'crisp-edges' }}
      />
      
      {/* Legenda */}
      <div className="absolute bottom-3 left-3 flex items-center gap-4 bg-concreto-950/80 backdrop-blur px-3 py-1.5 rounded text-[10px] font-mono text-aco-400">
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-ok inline-block" />
          posicao atual
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-sinal-500 inline-block" />
          waypoint
        </span>
        {frames.length > 0 && (
          <span className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full inline-block" style={{ backgroundColor: 'rgba(147, 197, 253, 0.85)' }} />
            foto
          </span>
        )}
        {(ancora1 || ancora2) && (
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-blue-500 inline-block" />
            ancoras A/B
          </span>
        )}
      </div>
    </div>
  )
}
