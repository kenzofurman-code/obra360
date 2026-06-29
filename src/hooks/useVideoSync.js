// src/hooks/useVideoSync.js
import { useState, useEffect, useCallback, useRef } from 'react'

/**
 * Interpola posição (x, y) na planta com base no tempo atual do vídeo.
 * Entre dois waypoints consecutivos, move o ponto suavemente.
 */
function interpolarPosicao(waypoints, tempoAtual) {
  if (!waypoints || waypoints.length === 0) return null
  if (waypoints.length === 1) return { x: waypoints[0].x, y: waypoints[0].y }

  const sorted = [...waypoints].sort((a, b) => a.t - b.t)

  // Antes do primeiro waypoint
  if (tempoAtual <= sorted[0].t) return { x: sorted[0].x, y: sorted[0].y }

  // Depois do último waypoint
  if (tempoAtual >= sorted[sorted.length - 1].t) {
    const last = sorted[sorted.length - 1]
    return { x: last.x, y: last.y }
  }

  // Entre dois waypoints: interpolação linear
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

export function useVideoSync(waypoints) {
  const [tempoAtual, setTempoAtual] = useState(0)
  const [duracao, setDuracao] = useState(0)
  const [posicao, setPosicao] = useState(null)
  const [waypointAtivo, setWaypointAtivo] = useState(null)
  const [player, setPlayer] = useState(null)
  const playerRef = useRef(null)

  // Atualiza posição toda vez que o tempo muda
  useEffect(() => {
    const pos = interpolarPosicao(waypoints, tempoAtual)
    setPosicao(pos)

    // Detecta qual waypoint está ativo (dentro de 3 segundos)
    if (waypoints && waypoints.length > 0) {
      const sorted = [...waypoints].sort((a, b) => a.t - b.t)
      let ativo = null
      for (const wp of sorted) {
        if (tempoAtual >= wp.t) ativo = wp
      }
      setWaypointAtivo(ativo)
    }
  }, [tempoAtual, waypoints])

  const registrarPlayer = useCallback((vjsPlayer) => {
    playerRef.current = vjsPlayer
    setPlayer(vjsPlayer)

    vjsPlayer.on('timeupdate', () => {
      setTempoAtual(vjsPlayer.currentTime())
    })
    vjsPlayer.on('durationchange', () => {
      setDuracao(vjsPlayer.duration())
    })
  }, [])

  // Clique na planta → pula para o waypoint mais próximo daquele ponto
  const pularParaWaypoint = useCallback((wp) => {
    if (playerRef.current && wp?.t !== undefined) {
      playerRef.current.currentTime(wp.t)
    }
  }, [])

  // Clique em coordenada da planta → encontra waypoint mais próximo e pula
  const pularParaCoordenada = useCallback((x, y) => {
    if (!waypoints || waypoints.length === 0 || !playerRef.current) return
    const closest = waypoints.reduce((best, wp) => {
      const dist = Math.sqrt((wp.x - x) ** 2 + (wp.y - y) ** 2)
      return dist < best.dist ? { wp, dist } : best
    }, { wp: null, dist: Infinity })

    if (closest.wp) {
      playerRef.current.currentTime(closest.wp.t)
    }
  }, [waypoints])

  return {
    tempoAtual,
    duracao,
    posicao,
    waypointAtivo,
    player,
    registrarPlayer,
    pularParaWaypoint,
    pularParaCoordenada,
  }
}
