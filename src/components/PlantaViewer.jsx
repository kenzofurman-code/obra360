// src/components/PlantaViewer.jsx
import { useRef, useEffect, useCallback } from 'react'

/**
 * Renderiza a planta PNG num canvas e sobrepõe:
 *  - Planta de outro pavimento sobreposta (com transparência e alinhamento geométrico)
 *  - Linha de trajetória entre waypoints
 *  - Cone de visão (FOV) alinhado com a rotação da câmera 360°
 *  - Ponto animado na posição atual (interpolada)
 *  - Pins clicáveis de cada waypoint
 *  - Âncoras de referência (pontos conhecidos)
 */
export default function PlantaViewer({
  plantaUrl,
  waypoints = [],
  posicao,
  waypointAtivo,
  onClickCoordenada,
  onClickWaypoint,
  player,
  headingOffset = 0,
  modoCalibrarAncoras = null, // null | 'ancora1' | 'ancora2'
  ancora1,
  ancora2,
  visitaSobreposta = null, // { planta_url, ancora1, ancora2 }
}) {
  const canvasRef = useRef(null)
  const imgRef = useRef(null)
  const imgSobrepostaRef = useRef(null)
  const animFrameRef = useRef(null)

  // Carrega imagem principal
  useEffect(() => {
    if (!plantaUrl) return
    const img = new Image()
    img.src = plantaUrl
    img.onload = () => {
      imgRef.current = img
      draw()
    }
  }, [plantaUrl])

  // Carrega imagem de sobreposição
  useEffect(() => {
    if (!visitaSobreposta?.planta_url) {
      imgSobrepostaRef.current = null
      return
    }
    const img = new Image()
    img.src = visitaSobreposta.planta_url
    img.onload = () => {
      imgSobrepostaRef.current = img
    }
  }, [visitaSobreposta])

  const toCanvas = useCallback((x, y, canvas) => ({
    cx: x * canvas.width,
    cy: y * canvas.height,
  }), [])

  const getCameraYaw = useCallback(() => {
    if (!player) return null
    try {
      const vr = player.vr?.()
      const camera = vr?.camera || vr?.camera_
      if (camera) {
        return camera.rotation.y
      }
    } catch (e) {
      // Falha silenciosa caso o plugin ainda não esteja inicializado
    }
    return null
  }, [player])

  const draw = useCallback(() => {
    const canvas = canvasRef.current
    if (!canvas) return
    const ctx = canvas.getContext('2d')
    const W = canvas.width
    const H = canvas.height

    ctx.clearRect(0, 0, W, H)

    // 1. Desenha Planta Principal
    if (imgRef.current) {
      ctx.drawImage(imgRef.current, 0, 0, W, H)
    } else {
      ctx.fillStyle = '#1a1c20'
      ctx.fillRect(0, 0, W, H)
      ctx.fillStyle = '#3c424d'
      ctx.font = '14px JetBrains Mono'
      ctx.textAlign = 'center'
      ctx.fillText('Carregando planta...', W / 2, H / 2)
    }

    // 2. Desenha Planta Sobreposta Alinhada Geometricamente (Procrustes 2D)
    if (imgSobrepostaRef.current && visitaSobreposta?.ancora1 && visitaSobreposta?.ancora2 && ancora1 && ancora2) {
      const ax1 = ancora1.x * W
      const ay1 = ancora1.y * H
      const ax2 = ancora2.x * W
      const ay2 = ancora2.y * H

      const bx1 = visitaSobreposta.ancora1.x * W
      const by1 = visitaSobreposta.ancora1.y * H
      const bx2 = visitaSobreposta.ancora2.x * W
      const by2 = visitaSobreposta.ancora2.y * H

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
        // Mapeia coordenadas: translação para origem da âncora 1, rotação, escala, translação de volta
        ctx.translate(ax1, ay1)
        ctx.rotate(rotation)
        ctx.scale(scale, scale)
        ctx.translate(-bx1, -by1)

        ctx.globalAlpha = 0.40 // 40% de opacidade
        ctx.drawImage(imgSobrepostaRef.current, 0, 0, W, H)
        ctx.restore()
      }
    }

    const sorted = [...waypoints].sort((a, b) => a.t - b.t)

    // 3. Desenha Linha de trajetória
    if (sorted.length > 1) {
      ctx.beginPath()
      ctx.strokeStyle = 'rgba(245, 158, 11, 0.4)'
      ctx.lineWidth = 2.5
      ctx.setLineDash([6, 4])
      sorted.forEach((wp, i) => {
        const { cx, cy } = toCanvas(wp.x, wp.y, canvas)
        if (i === 0) ctx.moveTo(cx, cy)
        else ctx.lineTo(cx, cy)
      })
      ctx.stroke()
      ctx.setLineDash([])
    }

    // 4. Desenha Cone de Visão (FOV) Sincronizado
    const yaw = getCameraYaw()
    if (posicao && yaw !== null) {
      const { cx, cy } = toCanvas(posicao.x, posicao.y, canvas)
      // Ajusta orientação (ângulo canvas = -yaw + offset - 90deg para Norte ser 0)
      const heading = -yaw + (headingOffset * Math.PI) / 180 - Math.PI / 2
      const radius = 60
      const aperture = (60 * Math.PI) / 180 // abertura de 60 graus

      const startAngle = heading - aperture / 2
      const endAngle = heading + aperture / 2

      ctx.beginPath()
      ctx.moveTo(cx, cy)
      ctx.arc(cx, cy, radius, startAngle, endAngle)
      ctx.closePath()

      const grad = ctx.createRadialGradient(cx, cy, 0, cx, cy, radius)
      grad.addColorStop(0, 'rgba(59, 130, 246, 0.5)')
      grad.addColorStop(0.3, 'rgba(59, 130, 246, 0.25)')
      grad.addColorStop(1, 'rgba(59, 130, 246, 0)')
      ctx.fillStyle = grad
      ctx.fill()

      // Desenha linhas de contorno do cone
      ctx.beginPath()
      ctx.moveTo(cx, cy)
      ctx.lineTo(cx + Math.cos(startAngle) * radius, cy + Math.sin(startAngle) * radius)
      ctx.moveTo(cx, cy)
      ctx.lineTo(cx + Math.cos(endAngle) * radius, cy + Math.sin(endAngle) * radius)
      ctx.strokeStyle = 'rgba(59, 130, 246, 0.3)'
      ctx.lineWidth = 1.5
      ctx.stroke()
    }

    // 5. Desenha Pins dos waypoints
    sorted.forEach((wp) => {
      const { cx, cy } = toCanvas(wp.x, wp.y, canvas)
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

    // 6. Desenha Ponto de posição atual
    if (posicao) {
      const { cx, cy } = toCanvas(posicao.x, posicao.y, canvas)
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

    // 7. Desenha Âncoras (Pontos conhecidos de calibração)
    if (ancora1) {
      const { cx, cy } = toCanvas(ancora1.x, ancora1.y, canvas)
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
      const { cx, cy } = toCanvas(ancora2.x, ancora2.y, canvas)
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
  }, [waypoints, posicao, waypointAtivo, toCanvas, getCameraYaw, headingOffset, ancora1, ancora2, visitaSobreposta])

  // Reanima continuamente para pulso e rotação de bússola
  useEffect(() => {
    const loop = () => {
      draw()
      animFrameRef.current = requestAnimationFrame(loop)
    }
    animFrameRef.current = requestAnimationFrame(loop)
    return () => cancelAnimationFrame(animFrameRef.current)
  }, [draw])

  const handleClick = useCallback((e) => {
    const canvas = canvasRef.current
    if (!canvas) return
    const rect = canvas.getBoundingClientRect()
    const x = (e.clientX - rect.left) / rect.width
    const y = (e.clientY - rect.top) / rect.height

    // Se está calibrando âncoras, chama o clique genérico
    if (modoCalibrarAncoras) {
      if (onClickCoordenada) onClickCoordenada(x, y)
      return
    }

    // 1. Primeiro verifica se clicou muito próximo de um pin (waypoint fixo com nota)
    const THRESH = 0.035
    const clicked = waypoints.find(
      wp => Math.sqrt((wp.x - x) ** 2 + (wp.y - y) ** 2) < THRESH
    )

    if (clicked && onClickWaypoint) {
      onClickWaypoint(clicked)
      return
    }

    // 2. Se não clicou num waypoint fixo, verifica se clicou em algum lugar ao longo do caminho (tipo Street View)
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

        // Projeta o clique P(x,y) no segmento de reta AB
        const apX = x - A.x
        const apY = y - A.y
        const r = Math.max(0, Math.min(1, (apX * abX + apY * abY) / abLen2))

        const cX = A.x + r * abX
        const cY = A.y + r * abY

        const dist = Math.sqrt((x - cX) ** 2 + (y - cY) ** 2)

        if (dist < minDistance) {
          minDistance = dist
          // Interpolação linear do tempo do vídeo baseado na posição proporcional no segmento
          bestTime = A.t + r * (B.t - A.t)
        }
      }

      // Limiar de proximidade de clique ao traçado (0.025 = 2.5% do tamanho do canvas)
      const PATH_THRESH = 0.025
      if (minDistance < PATH_THRESH && bestTime !== null) {
        player.currentTime(bestTime)
        return
      }
    }

    // 3. Caso contrário, trata como um clique em coordenada livre (para novos waypoints/âncoras)
    if (onClickCoordenada) {
      onClickCoordenada(x, y)
    }
  }, [waypoints, onClickCoordenada, onClickWaypoint, modoCalibrarAncoras, player])

  return (
    <div className="relative w-full h-full rounded-lg overflow-hidden bg-concreto-900 border border-concreto-700">
      <canvas
        ref={canvasRef}
        width={900}
        height={600}
        className="w-full h-full cursor-crosshair"
        onClick={handleClick}
        style={{ imageRendering: 'crisp-edges' }}
      />
      {/* Legenda */}
      <div className="absolute bottom-3 left-3 flex items-center gap-4 bg-concreto-950/80 backdrop-blur px-3 py-1.5 rounded text-[10px] font-mono text-aco-400">
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-ok inline-block" />
          posição atual
        </span>
        <span className="flex items-center gap-1.5">
          <span className="w-2 h-2 rounded-full bg-sinal-500 inline-block" />
          waypoint
        </span>
        {(ancora1 || ancora2) && (
          <span className="flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-blue-500 inline-block" />
            âncoras A/B
          </span>
        )}
      </div>
    </div>
  )
}
