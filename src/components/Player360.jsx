// src/components/Player360.jsx
import { useEffect, useRef, useState } from 'react'
import videojs from 'video.js'
import * as THREE from 'three'

/**
 * Interpola posição (x, y) na planta com base no tempo do vídeo.
 * Executado diretamente a 60fps para evitar o throttle de 4Hz do player.
 */
function interpolarPosicao(waypoints, tempoAtual) {
  if (!waypoints || waypoints.length === 0) return null
  if (waypoints.length === 1) return { x: waypoints[0].x, y: waypoints[0].y }

  const sorted = [...waypoints].sort((a, b) => a.t - b.t)

  if (tempoAtual <= sorted[0].t) return { x: sorted[0].x, y: sorted[0].y }
  if (tempoAtual >= sorted[sorted.length - 1].t) {
    const last = sorted[sorted.length - 1]
    return { x: last.x, y: last.y }
  }

  for (let i = 0; i < sorted.length - 1; i++) {
    const a = sorted[i]
    const b = sorted[i + 1]
    if (tempoAtual >= a.t && tempoAtual <= b.t) {
      const progress = (tempoAtual - a.t) / (b.t - a.t)
      return {
        x: a.x + (b.x - a.x) * progress,
        y: a.y + (b.y - a.y) * progress,
      }
    }
  }
  return null
}

/**
 * Player 360° com HLS via Video.js + plugin VR.
 * Adicionalmente projeta a passarela 3D no chão do vídeo como um Ribbon (Fita) estável.
 */
export default function Player360({
  hlsUrl,
  onReady,
  autoplay = false,
  waypoints = [],
  posicao = null, // mantido para compatibilidade de prop
  headingOffset = 0,
  lineOpacity = 80,
  lineThickness = 1.0,
  espelharCaminho = false,
  ribbonScale = 1.0,
  ribbonRotationOffset = 0,
}) {
  const videoRef = useRef(null)
  const playerRef = useRef(null)
  const animFrameRef = useRef(null)
  const line3DRef = useRef(null)
  const sceneRef = useRef(null)
  const [playerReady, setPlayerReady] = useState(false)

  // Estado persistente de renderização da posição da câmera (para suavização LERP)
  const renderPosRef = useRef({ x: 0, y: 0, initialized: false })
  const lastTimeRef = useRef(0)

  // Refs para manter os dados atualizados no loop de 60fps sem recriar o effect
  const waypointsRef = useRef(waypoints)
  const headingOffsetRef = useRef(headingOffset)
  const lineOpacityRef = useRef(lineOpacity)
  const lineThicknessRef = useRef(lineThickness)
  const espelharCaminhoRef = useRef(espelharCaminho)
  const ribbonScaleRef = useRef(ribbonScale)
  const ribbonRotationRef = useRef(ribbonRotationOffset)

  // Sincroniza props com as refs
  useEffect(() => { waypointsRef.current = waypoints }, [waypoints])
  useEffect(() => { headingOffsetRef.current = headingOffset }, [headingOffset])
  useEffect(() => { lineOpacityRef.current = lineOpacity }, [lineOpacity])
  useEffect(() => { lineThicknessRef.current = lineThickness }, [lineThickness])
  useEffect(() => { espelharCaminhoRef.current = espelharCaminho }, [espelharCaminho])
  useEffect(() => { ribbonScaleRef.current = ribbonScale }, [ribbonScale])
  useEffect(() => { ribbonRotationRef.current = ribbonRotationOffset }, [ribbonRotationOffset])

  // Inicialização do Player
  useEffect(() => {
    let active = true
    let player = null

    const initPlayer = async () => {
      window.videojs = videojs
      await import('videojs-vr')

      if (!active) return
      if (!videoRef.current || !hlsUrl) return

      const isHls = hlsUrl.includes('.m3u8')
      const type = isHls ? 'application/x-mpegURL' : 'video/mp4'

      player = videojs(videoRef.current, {
        controls: true,
        autoplay,
        preload: 'auto',
        fluid: true,
        html5: {
          vhs: {
            overrideNative: true,
            enableLowInitialPlaylist: true,
          },
        },
        sources: [{ src: hlsUrl, type }],
      })

      player.vr({
        projection: '360',
        motionControls: false,
        debug: false,
      })

      playerRef.current = player
      setPlayerReady(true)

      player.ready(() => {
        if (onReady && active) onReady(player)
      })
    }

    initPlayer()

    return () => {
      active = false
      setPlayerReady(false)
      if (player && !player.isDisposed()) {
        player.dispose()
      }
      playerRef.current = null
    }
  }, [hlsUrl, autoplay]) // eslint-disable-line

  // Loop de Renderização Estável da Passarela 3D
  // Roda apenas uma vez quando o player está pronto e limpa ao desmontar
  useEffect(() => {
    if (!playerReady) return
    const player = playerRef.current
    if (!player) return

    const update3DLine = () => {
      try {
        const vr = player.vr?.()
        const scene = vr?.scene || vr?.scene_
        
        if (scene) {
          sceneRef.current = scene
          
          const currentWaypoints = waypointsRef.current
          const t_now = player.currentTime() // Obtém tempo de vídeo não-bloqueado

          // Calcula posição interpolada a 60fps
          const pos_now = interpolarPosicao(currentWaypoints, t_now)

          if (pos_now) {
            // Suavização temporal (LERP)
            // Se for o início do vídeo ou um pulo grande (scrubbing > 1.5s), teleporta a câmera
            if (!renderPosRef.current.initialized || Math.abs(t_now - lastTimeRef.current) > 1.5) {
              renderPosRef.current.x = pos_now.x
              renderPosRef.current.y = pos_now.y
              renderPosRef.current.initialized = true
            } else {
              // Aplica amortecimento de 18% por frame
              renderPosRef.current.x += (pos_now.x - renderPosRef.current.x) * 0.18
              renderPosRef.current.y += (pos_now.y - renderPosRef.current.y) * 0.18
            }
            lastTimeRef.current = t_now

            // Cria o mesh apenas UMA vez, persistindo na cena
            if (!line3DRef.current) {
              const material = new THREE.MeshBasicMaterial({
                color: 0x3b82f6, // azul elétrico/neon
                transparent: true,
                opacity: lineOpacityRef.current / 100,
                side: THREE.DoubleSide,
                depthWrite: false, // evita piscar sob texturas do chao
              })
              const geometry = new THREE.BufferGeometry()
              const mesh = new THREE.Mesh(geometry, material)
              scene.add(mesh)
              line3DRef.current = mesh
            }

            const mesh = line3DRef.current
            
            // Atualiza opacidade em tempo real
            mesh.material.opacity = lineOpacityRef.current / 100
            
            const xc = renderPosRef.current.x
            const yc = renderPosRef.current.y
            
            // A passarela 3D é desenhada diretamente no espaço de coordenadas do vídeo (video space).
            // Por padrão o alinhamento 3D é nativo (ângulo 0, ribbonRotationOffset=0) já que a
            // odometria visual e o vídeo vem da mesma gravação; ribbonRotationOffset existe como
            // ajuste manual pra casos excepcionais. A bússola (headingOffset) serve apenas para
            // orientar o trajeto 2D na planta baixa física.

            // 1. Gera os pontos centrais 3D
            const sorted = [...currentWaypoints].sort((a, b) => a.t - b.t)
            const pathPoints = sorted.map(wp => {
              const escala = 22 * ribbonScaleRef.current
              const rawDx = (wp.x - xc) * escala
              const dx0 = espelharCaminhoRef.current ? -rawDx : rawDx
              const dy0 = (wp.y - yc) * escala

              // Ajuste manual de rotacao (por padrao 0 - o video real e a trajetoria
              // vem da mesma gravacao, entao alinham nativamente; so usar se precisar
              // corrigir algum caso excepcional).
              const rad = (ribbonRotationRef.current * Math.PI) / 180
              const dx = dx0 * Math.cos(rad) - dy0 * Math.sin(rad)
              const dy = dx0 * Math.sin(rad) + dy0 * Math.cos(rad)

              // No espaço 3D do vídeo, X é horizontal (rx = dx)
              // E a câmera olha para a direção Z negativa. Logo, andar para frente (+dy)
              // deve nos mover para frente no espaço 3D (Z negativo: rz = -dy)
              const rx = dx
              const rz = -dy
              const ry = -2.1 // Altura fisica fixa do chao
              return new THREE.Vector3(rx, ry, rz)
            })

            // 2. Calcula as bordas para a espessura da fita
            const vertices = []
            const halfWidth = 0.18 * lineThicknessRef.current

            for (let i = 0; i < pathPoints.length; i++) {
              const p = pathPoints[i]
              let dir = new THREE.Vector3()
              if (i < pathPoints.length - 1) {
                dir.subVectors(pathPoints[i + 1], p)
              } else {
                dir.subVectors(p, pathPoints[i - 1])
              }

              const len = Math.sqrt(dir.x * dir.x + dir.z * dir.z)
              let px = 0, pz = 1
              if (len > 0) {
                px = -dir.z / len
                pz = dir.x / len
              }

              vertices.push(p.x - px * halfWidth, p.y, p.z - pz * halfWidth)
              vertices.push(p.x + px * halfWidth, p.y, p.z + pz * halfWidth)
            }

            // 3. Monta os indices de triangulo
            const indices = []
            for (let i = 0; i < pathPoints.length - 1; i++) {
              const v0 = 2 * i
              const v1 = 2 * i + 1
              const v2 = 2 * (i + 1)
              const v3 = 2 * (i + 1) + 1

              indices.push(v0, v1, v2)
              indices.push(v1, v3, v2)
            }

            // 4. Atualiza os buffers de geometria existentes em-lugar
            const geometry = mesh.geometry
            geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3))
            geometry.setIndex(indices)
            geometry.computeVertexNormals()
            geometry.attributes.position.needsUpdate = true
            if (geometry.index) geometry.index.needsUpdate = true
          }
        }
      } catch (e) {
        // Ignora erros de inicializacao
      }

      animFrameRef.current = requestAnimationFrame(update3DLine)
    }

    animFrameRef.current = requestAnimationFrame(update3DLine)

    return () => {
      cancelAnimationFrame(animFrameRef.current)
      if (line3DRef.current && sceneRef.current) {
        sceneRef.current.remove(line3DRef.current)
        line3DRef.current = null
      }
    }
  }, [playerReady]) // Roda apenas uma vez ao iniciar e limpa ao desmontar

  if (!hlsUrl) {
    return (
      <div className="flex items-center justify-center w-full h-full bg-concreto-900 rounded-lg text-aco-400 text-sm font-mono">
        Nenhum vídeo selecionado
      </div>
    )
  }

  const crossOrigin = hlsUrl?.startsWith('blob:') ? undefined : 'anonymous'

  return (
    <div className="w-full h-full rounded-lg overflow-hidden bg-black">
      <div data-vjs-player>
        <video
          ref={videoRef}
          className="video-js vjs-default-skin vjs-big-play-centered w-full h-full"
          playsInline
          crossOrigin={crossOrigin}
        />
      </div>
    </div>
  )
}
