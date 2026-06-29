// src/components/Player360.jsx
import { useEffect, useRef } from 'react'
import videojs from 'video.js'
import 'videojs-vr'

/**
 * Player 360° com HLS via Video.js + plugin VR.
 * Props:
 *   hlsUrl    — URL pública do index.m3u8 no Cloudflare R2
 *   onReady   — callback(player) chamado quando o player está pronto
 *   autoplay  — boolean (default false)
 */
export default function Player360({ hlsUrl, onReady, autoplay = false }) {
  const videoRef = useRef(null)
  const playerRef = useRef(null)

  useEffect(() => {
    if (!videoRef.current || !hlsUrl) return

    // Evita inicializar duas vezes (React StrictMode)
    if (playerRef.current) return

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
      sources: [{ src: hlsUrl, type: 'application/x-mpegURL' }],
    })

    // Ativa o plugin VR (equiretangular 360°)
    player.vr({
      projection: '360',
      motionControls: false, // desativa giroscópio no escritório
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
