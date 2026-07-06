// src/components/Player360.jsx
import { useEffect, useRef } from 'react'
import videojs from 'video.js'
import * as THREE from 'three'
import 'videojs-vr'

/**
 * Player 360° com HLS via Video.js + plugin VR.
 * Adicionalmente projeta a passarela 3D no chão do vídeo como um Ribbon (Fita) ajustável.
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
}) {
  const videoRef = useRef(null)
  const playerRef = useRef(null)
  const animFrameRef = useRef(null)
  const line3DRef = useRef(null)
  const sceneRef = useRef(null)

  // Inicialização do Player
  useEffect(() => {
    if (!videoRef.current || !hlsUrl) return
    if (playerRef.current) return

    const isHls = hlsUrl.includes('.m3u8')
    const type = isHls ? 'application/x-mpegURL' : 'video/mp4'

    const player = videojs(videoRef.current, {
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

    // Ativa o plugin VR (equiretangular 360°)
    player.vr({
      projection: '360',
      motionControls: false, // desativa giroscópio no desktop
      debug: false,
    })

    playerRef.current = player

    player.ready(() => {
      if (onReady) onReady(player)
    })

    return () => {
      if (playerRef.current && !playerRef.current.isDisposed()) {
        playerRef.current.dispose()
        playerRef.current = null
      }
    }
  }, [hlsUrl]) // eslint-disable-line

  // Loop de Renderização / Atualização da Passarela 3D no chão do vídeo
  useEffect(() => {
    const player = playerRef.current
    if (!player) return

    const update3DLine = () => {
      try {
        const vr = player.vr?.()
        const scene = vr?.scene || vr?.scene_
        
        if (scene && waypoints && waypoints.length >= 2 && posicao) {
          sceneRef.current = scene

          // Se o Ribbon Mesh (fita) ainda não existe na cena, cria
          if (!line3DRef.current) {
            const material = new THREE.MeshBasicMaterial({
              color: 0x3b82f6, // azul elétrico/neon
              transparent: true,
              opacity: lineOpacity / 100,
              side: THREE.DoubleSide,
              depthWrite: false, // evita piscar ou sumir sob o fundo do vídeo
            })
            const geometry = new THREE.BufferGeometry()
            const mesh = new THREE.Mesh(geometry, material)
            scene.add(mesh)
            line3DRef.current = mesh
          }

          // Atualiza as propriedades e os vértices do Ribbon
          if (line3DRef.current) {
            // Atualiza opacidade do material
            line3DRef.current.material.opacity = lineOpacity / 100;
            
            const xc = posicao.x
            const yc = posicao.y
            
            // Converte o Azimute (Norte) de graus para radianos
            const alpha = (headingOffset * Math.PI) / 180
            const cos = Math.cos(alpha)
            const sin = Math.sin(alpha)

            // 1. Gera os pontos centrais tridimensionais do caminho no chão
            const sorted = [...waypoints].sort((a, b) => a.t - b.t)
            const pathPoints = sorted.map(wp => {
              const dx = (wp.x - xc) * 22
              const dy = (wp.y - yc) * 22
              const rx = dx * cos - dy * sin
              const rz = dx * sin + dy * cos
              const ry = -2.1 // Altura física aproximada do chão (-2.1 unidades abaixo da lente)
              return new THREE.Vector3(rx, ry, rz)
            })

            // 2. Calcula as bordas esquerda/direita da fita para gerar a espessura
            const vertices = []
            const halfWidth = 0.18 * lineThickness // largura base de 0.18 unidades escalada

            for (let i = 0; i < pathPoints.length; i++) {
              const p = pathPoints[i]
              let dir = new THREE.Vector3()
              if (i < pathPoints.length - 1) {
                dir.subVectors(pathPoints[i + 1], p)
              } else {
                dir.subVectors(p, pathPoints[i - 1])
              }

              // Vetor perpendicular no plano XZ
              const len = Math.sqrt(dir.x * dir.x + dir.z * dir.z)
              let px = 0, pz = 1
              if (len > 0) {
                px = -dir.z / len
                pz = dir.x / len
              }

              // Vértice Esquerdo (A)
              vertices.push(p.x - px * halfWidth, p.y, p.z - pz * halfWidth)
              // Vértice Direito (B)
              vertices.push(p.x + px * halfWidth, p.y, p.z + pz * halfWidth)
            }

            // 3. Monta a lista de triângulos para formar as faces
            const indices = []
            for (let i = 0; i < pathPoints.length - 1; i++) {
              const v0 = 2 * i
              const v1 = 2 * i + 1
              const v2 = 2 * (i + 1)
              const v3 = 2 * (i + 1) + 1

              // Triângulo 1 (v0 -> v1 -> v2)
              indices.push(v0, v1, v2)
              // Triângulo 2 (v1 -> v3 -> v2)
              indices.push(v1, v3, v2)
            }

            const geometry = line3DRef.current.geometry
            geometry.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3))
            geometry.setIndex(indices)
            geometry.computeVertexNormals()
            geometry.attributes.position.needsUpdate = true
            if (geometry.index) geometry.index.needsUpdate = true
          }
        }
      } catch (e) {
        // Ignora erros temporários durante o carregamento inicial
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
  }, [waypoints, posicao, headingOffset, lineOpacity, lineThickness])

  if (!hlsUrl) {
    return (
      <div className="flex items-center justify-center w-full h-full bg-concreto-900 rounded-lg text-aco-400 text-sm font-mono">
        Nenhum vídeo selecionado
      </div>
    )
  }

  return (
    <div className="w-full h-full rounded-lg overflow-hidden bg-black">
      <div data-vjs-player>
        <video
          ref={videoRef}
          className="video-js vjs-default-skin vjs-big-play-centered w-full h-full"
          playsInline
          crossOrigin="anonymous"
        />
      </div>
    </div>
  )
}
