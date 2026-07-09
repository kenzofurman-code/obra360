// src/components/Player360.jsx
import { useEffect, useRef, useState } from 'react'
import videojs from 'video.js'
import * as THREE from 'three'

/**
 * Player 360° com HLS via Video.js + plugin VR.
 * Adicionalmente projeta a passarela 3D no chão do vídeo como um Ribbon (Fita) estável.
 */
export default function Player360({
  hlsUrl,
  onReady,
  autoplay = false,
  waypoints = [],
  posicao = null,
  headingOffset = 0,
  lineOpacity = 80,
  lineThickness = 1.0,
  espelharCaminho = false,
}) {
  const videoRef = useRef(null)
  const playerRef = useRef(null)
  const animFrameRef = useRef(null)
  const line3DRef = useRef(null)
  const sceneRef = useRef(null)
  const [playerReady, setPlayerReady] = useState(false)

  // Refs para manter os dados atualizados no loop de 60fps sem recriar o effect
  const waypointsRef = useRef(waypoints)
  const posicaoRef = useRef(posicao)
  const headingOffsetRef = useRef(headingOffset)
  const lineOpacityRef = useRef(lineOpacity)
  const lineThicknessRef = useRef(lineThickness)
  const espelharCaminhoRef = useRef(espelharCaminho)

  // Sincroniza props com as refs
  useEffect(() => { waypointsRef.current = waypoints }, [waypoints])
  useEffect(() => { posicaoRef.current = posicao }, [posicao])
  useEffect(() => { headingOffsetRef.current = headingOffset }, [headingOffset])
  useEffect(() => { lineOpacityRef.current = lineOpacity }, [lineOpacity])
  useEffect(() => { lineThicknessRef.current = lineThickness }, [lineThickness])
  useEffect(() => { espelharCaminhoRef.current = espelharCaminho }, [espelharCaminho])

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
  // Roda apenas uma vez quando o player está pronto e limpa apenas ao desmontar
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
          const currentPosicao = posicaoRef.current

          if (currentWaypoints && currentWaypoints.length >= 2 && currentPosicao) {
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
            
            const xc = currentPosicao.x
            const yc = currentPosicao.y
            
            const alpha = (headingOffsetRef.current * Math.PI) / 180
            const cos = Math.cos(alpha)
            const sin = Math.sin(alpha)

            // 1. Gera os pontos centrais 3D
            const sorted = [...currentWaypoints].sort((a, b) => a.t - b.t)
            const pathPoints = sorted.map(wp => {
              const rawDx = (wp.x - xc) * 22
              const dx = espelharCaminhoRef.current ? -rawDx : rawDx
              const dy = (wp.y - yc) * 22
              const rx = dx * cos - dy * sin
              const rz = dx * sin + dy * cos
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

            // 4. Atualiza os buffers de geometria existentes em-lugar (sem destruir o mesh)
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
