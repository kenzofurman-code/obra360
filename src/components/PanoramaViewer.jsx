// src/components/PanoramaViewer.jsx
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'

/**
 * Interpola posição (x, y) na planta com base no "tempo de vídeo" simulado.
 * Idêntico ao Player360.jsx - os waypoints usam a mesma linha do tempo dos quadros
 * (ambos derivados do mesmo vídeo/trajetória original no worker.py).
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
 * "Player" falso com a mesma API mínima do video.js (currentTime/duration/paused/
 * play/pause/playbackRate/on/off/vr) que o resto do app já consome (useVideoSync.js,
 * os controles de play/pause/frame em Visita.jsx, o clique-no-trajeto em PlantaViewer.jsx).
 * Isso permite trocar Player360 (vídeo) por PanoramaViewer (fotos 360°) sem tocar em
 * nenhum desses três lugares - eles continuam achando que estão controlando um vídeo.
 */
class FakePlayer {
  constructor() {
    this._listeners = {}
    this._time = 0
    this._duration = 0
    this._paused = true
    this._rate = 1
    this.camera = null
  }
  on(evt, cb) {
    if (!this._listeners[evt]) this._listeners[evt] = []
    this._listeners[evt].push(cb)
  }
  off(evt, cb) {
    if (!this._listeners[evt]) return
    this._listeners[evt] = this._listeners[evt].filter((f) => f !== cb)
  }
  _emit(evt) {
    ;(this._listeners[evt] || []).forEach((cb) => cb())
  }
  currentTime(t) {
    if (t === undefined) return this._time
    this._time = Math.max(0, Math.min(this._duration, t))
    this._emit('timeupdate')
    return this._time
  }
  duration(d) {
    if (d === undefined) return this._duration
    this._duration = d
    this._emit('durationchange')
    return this._duration
  }
  paused() {
    return this._paused
  }
  play() {
    this._paused = false
    this._emit('play')
  }
  pause() {
    this._paused = true
    this._emit('pause')
  }
  playbackRate(v) {
    if (v === undefined) return this._rate
    this._rate = v
    return this._rate
  }
  vr() {
    return { camera: this.camera }
  }
  isDisposed() {
    return false
  }
  dispose() {
    this._listeners = {}
  }
}

/**
 * Viewer de panoramas 360° (fotos equiretangulares) gerado a partir do manifest.json
 * do worker.py/gerar_quadros.py, substituindo o Player360 (vídeo) quando a vistoria
 * já tem `manifest_url`. Faz preload dos vizinhos + crossfade entre quadros, olhar-ao-redor
 * por arraste, zoom por scroll, e desenha a mesma passarela 3D do Player360.
 */
export default function PanoramaViewer({
  manifestUrl,
  onReady,
  autoplay = false,
  waypoints = [],
  headingOffset = 0,
  lineOpacity = 80,
  lineThickness = 1.0,
  espelharCaminho = false,
  ribbonScale = 1.0, // multiplica o fator de escala base (22) da passarela 3D
  ribbonRotationOffset = 0, // graus - gira a passarela 3D em torno do ponto atual antes de desenhar
}) {
  const containerRef = useRef(null)
  const fakePlayerRef = useRef(null)
  const quadrosRef = useRef([])
  const [quadros, setQuadros] = useState(null) // null = carregando, [] = manifest vazio
  const [erro, setErro] = useState(null)

  // Refs de props para uso dentro do loop 3D sem precisar recriar o effect de cena
  const waypointsRef = useRef(waypoints)
  const lineOpacityRef = useRef(lineOpacity)
  const lineThicknessRef = useRef(lineThickness)
  const espelharCaminhoRef = useRef(espelharCaminho)
  const ribbonScaleRef = useRef(ribbonScale)
  const ribbonRotationRef = useRef(ribbonRotationOffset)
  useEffect(() => { waypointsRef.current = waypoints }, [waypoints])
  useEffect(() => { lineOpacityRef.current = lineOpacity }, [lineOpacity])
  useEffect(() => { lineThicknessRef.current = lineThickness }, [lineThickness])
  useEffect(() => { espelharCaminhoRef.current = espelharCaminho }, [espelharCaminho])
  useEffect(() => { ribbonScaleRef.current = ribbonScale }, [ribbonScale])
  useEffect(() => { ribbonRotationRef.current = ribbonRotationOffset }, [ribbonRotationOffset])
  // headingOffset mantido na assinatura por paridade com Player360 (orienta a planta 2D,
  // não o vídeo/panorama - ver comentário equivalente em Player360.jsx)
  void headingOffset

  // 1. Carrega o manifest.json do R2
  useEffect(() => {
    let active = true
    setQuadros(null)
    setErro(null)
    if (!manifestUrl) return
    fetch(manifestUrl)
      .then((r) => {
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        return r.json()
      })
      .then((data) => {
        if (!active) return
        const lista = [...(data.quadros || [])].sort((a, b) => a.t - b.t)
        quadrosRef.current = lista
        setQuadros(lista)
      })
      .catch((e) => {
        if (active) setErro(e.message || String(e))
      })
    return () => { active = false }
  }, [manifestUrl])

  // 2. Inicializa a cena Three.js + o FakePlayer quando os quadros chegam
  useEffect(() => {
    if (!quadros || quadros.length === 0) return
    const container = containerRef.current
    if (!container) return

    let active = true
    // pasta onde o manifest.json vive no R2 - os `arquivo` dos quadros são relativos a ela
    const baseUrl = manifestUrl.replace(/[^/]*$/, '')
    const resolveUrl = (arquivo) => {
      if (!arquivo) return null
      return /^https?:\/\//.test(arquivo) ? arquivo : baseUrl + arquivo
    }

    // --- Setup Three.js ---
    const scene = new THREE.Scene()
    const camera = new THREE.PerspectiveCamera(
      75, container.clientWidth / Math.max(1, container.clientHeight), 1, 1100
    )
    const renderer = new THREE.WebGLRenderer({ antialias: true })
    renderer.setPixelRatio(window.devicePixelRatio)
    renderer.setSize(container.clientWidth, container.clientHeight)
    container.appendChild(renderer.domElement)

    // Duas esferas sobrepostas (double-buffer) pra permitir crossfade suave ao trocar de quadro
    const geometry = new THREE.SphereGeometry(500, 60, 40)
    geometry.scale(-1, 1, 1) // inverte a esfera pra ver a textura por dentro

    const matA = new THREE.MeshBasicMaterial({ transparent: true, opacity: 1, depthWrite: false })
    const matB = new THREE.MeshBasicMaterial({ transparent: true, opacity: 0, depthWrite: false })
    const sphereA = new THREE.Mesh(geometry, matA)
    const sphereB = new THREE.Mesh(geometry, matB)
    // renderOrder EXPLICITO (nao confiar no sort automatico do three.js aqui): a
    // camera fica sempre no centro exato das esferas (raio 500), entao a distancia
    // camera->centro-da-esfera usada pelo sort de transparencia da ~0 - o three.js
    // interpreta isso como "objeto mais proximo" e pode desenhar a esfera DEPOIS da
    // passarela (que fica a ~dezenas de unidades, nao 500), cobrindo-a por cima na
    // maior parte do tempo. So aparecia de relance durante o crossfade porque as
    // esferas ficavam parcialmente transparentes (opacity<1) e deixavam a passarela
    // (desenhada por baixo) vazar. renderOrder tem prioridade sobre esse sort por
    // distancia, entao forcamos aqui: esferas primeiro, passarela por cima sempre.
    sphereA.renderOrder = 0
    sphereB.renderOrder = 0
    scene.add(sphereA)
    scene.add(sphereB)

    const textureLoader = new THREE.TextureLoader()
    textureLoader.setCrossOrigin('anonymous')
    const cacheTexturas = new Map() // url -> THREE.Texture

    const carregarTextura = (url) => new Promise((resolve, reject) => {
      if (!url) { reject(new Error('sem url')); return }
      if (cacheTexturas.has(url)) { resolve(cacheTexturas.get(url)); return }
      textureLoader.load(
        url,
        (tex) => {
          tex.colorSpace = THREE.SRGBColorSpace
          cacheTexturas.set(url, tex)
          // limita o cache pra nao estourar memoria em vistorias longas (muitos quadros)
          if (cacheTexturas.size > 12) {
            const primeiraChave = cacheTexturas.keys().next().value
            if (primeiraChave !== url) {
              cacheTexturas.get(primeiraChave)?.dispose()
              cacheTexturas.delete(primeiraChave)
            }
          }
          resolve(tex)
        },
        undefined,
        reject
      )
    })

    // Estado de exibição atual
    let frenteEhA = true
    let indiceAtualExibido = -1
    let transicaoAtiva = false

    const mostrarQuadro = async (indice) => {
      if (indice === indiceAtualExibido || transicaoAtiva) return
      const quadro = quadrosRef.current[indice]
      const url = resolveUrl(quadro?.arquivo)
      if (!url) return
      let tex
      try {
        tex = await carregarTextura(url)
      } catch (e) {
        return // esse quadro falhou ao carregar - mantém o panorama anterior na tela
      }
      if (!active) return
      indiceAtualExibido = indice

      const matVisivel = frenteEhA ? matA : matB
      const matOculto = frenteEhA ? matB : matA
      matOculto.map = tex
      matOculto.needsUpdate = true

      // Crossfade rápido entre o panorama antigo e o novo
      const duracaoFade = 250
      const t0 = performance.now()
      transicaoAtiva = true
      const passoFade = () => {
        const p = Math.min(1, (performance.now() - t0) / duracaoFade)
        matOculto.opacity = p
        matVisivel.opacity = 1 - p
        if (p < 1) {
          requestAnimationFrame(passoFade)
        } else {
          frenteEhA = !frenteEhA
          transicaoAtiva = false
        }
      }
      requestAnimationFrame(passoFade)

      // Preload dos vizinhos (anterior/próximo) pra transição instantânea ao navegar
      ;[indice - 1, indice + 1].forEach((i) => {
        const q = quadrosRef.current[i]
        if (q) carregarTextura(resolveUrl(q.arquivo)).catch(() => {})
      })
    }

    // Busca binária pelo quadro com t mais próximo do tempo alvo (lista ordenada por t)
    const encontrarIndicePorTempo = (t) => {
      const lista = quadrosRef.current
      if (lista.length === 0) return -1
      if (t <= lista[0].t) return 0
      if (t >= lista[lista.length - 1].t) return lista.length - 1
      let lo = 0, hi = lista.length - 1
      while (lo < hi) {
        const mid = (lo + hi) >> 1
        if (lista[mid].t < t) lo = mid + 1
        else hi = mid
      }
      const depois = lo
      const antes = Math.max(0, lo - 1)
      return (t - lista[antes].t) <= (lista[depois].t - t) ? antes : depois
    }

    // --- FakePlayer: ponte com useVideoSync / controles de play-pause / PlantaViewer ---
    const fp = new FakePlayer()
    fp.camera = camera
    fakePlayerRef.current = fp
    const total = quadrosRef.current[quadrosRef.current.length - 1]?.t || 0
    fp.duration(total)

    const aoMudarTempo = () => {
      const idx = encontrarIndicePorTempo(fp.currentTime())
      if (idx >= 0) mostrarQuadro(idx)
    }
    fp.on('timeupdate', aoMudarTempo)
    mostrarQuadro(0) // exibe o primeiro quadro imediatamente

    // --- Olhar ao redor (arraste) + zoom (scroll) - técnica clássica de panorama Three.js ---
    let lon = 0, lat = 0
    let isUserInteracting = false
    let onDownMouseX = 0, onDownMouseY = 0, onDownLon = 0, onDownLat = 0

    const onPointerDown = (e) => {
      isUserInteracting = true
      onDownMouseX = e.clientX
      onDownMouseY = e.clientY
      onDownLon = lon
      onDownLat = lat
    }
    const onPointerMove = (e) => {
      if (!isUserInteracting) return
      lon = (onDownMouseX - e.clientX) * 0.15 + onDownLon
      lat = (e.clientY - onDownMouseY) * 0.15 + onDownLat
    }
    const onPointerUp = () => { isUserInteracting = false }
    const onWheel = (e) => {
      e.preventDefault()
      camera.fov = THREE.MathUtils.clamp(camera.fov + e.deltaY * 0.05, 30, 100)
      camera.updateProjectionMatrix()
    }

    renderer.domElement.addEventListener('pointerdown', onPointerDown)
    window.addEventListener('pointermove', onPointerMove)
    window.addEventListener('pointerup', onPointerUp)
    renderer.domElement.addEventListener('wheel', onWheel, { passive: false })

    // --- Passarela 3D (mesma matemática/constantes do Player360.jsx) ---
    const renderPos = { x: 0, y: 0, initialized: false }
    let lastTimeRibbon = 0
    let line3D = null

    const atualizarPassarela = () => {
      const t_now = fp.currentTime()
      const pos_now = interpolarPosicao(waypointsRef.current, t_now)
      if (!pos_now) return
      // com <2 pontos não dá pra formar segmentos (indices ficaria vazio) - a fita
      // ficaria "invisível" sem erro nenhum no console, então corta aqui de propósito
      if (!waypointsRef.current || waypointsRef.current.length < 2) return

      if (!renderPos.initialized || Math.abs(t_now - lastTimeRibbon) > 1.5) {
        renderPos.x = pos_now.x
        renderPos.y = pos_now.y
        renderPos.initialized = true
      } else {
        renderPos.x += (pos_now.x - renderPos.x) * 0.18
        renderPos.y += (pos_now.y - renderPos.y) * 0.18
      }
      lastTimeRibbon = t_now

      if (!line3D) {
        const material = new THREE.MeshBasicMaterial({
          color: 0x3b82f6,
          transparent: true,
          opacity: lineOpacityRef.current / 100,
          side: THREE.DoubleSide,
          depthWrite: false,
        })
        const geo = new THREE.BufferGeometry()
        line3D = new THREE.Mesh(geo, material)
        // se a geometria ficar com coordenada NaN/Infinity em algum frame (dado bruto
        // ruim), o bounding sphere fica inválido e o three.js corta (culling) a fita da
        // frustum silenciosamente, sem erro. Desliga o culling pra ela nunca "sumir" assim.
        line3D.frustumCulled = false
        // renderOrder maior que as esferas (0) - garante que a passarela sempre
        // desenha por cima delas (ver nota em sphereA/sphereB acima).
        line3D.renderOrder = 1
        scene.add(line3D)
      }

      line3D.material.opacity = lineOpacityRef.current / 100

      const xc = renderPos.x
      const yc = renderPos.y
      const sorted = [...waypointsRef.current]
        .filter((wp) => Number.isFinite(wp?.x) && Number.isFinite(wp?.y) && Number.isFinite(wp?.t))
        .sort((a, b) => a.t - b.t)
      if (sorted.length < 2) return
      const pathPoints = sorted.map((wp) => {
        const escala = 22 * ribbonScaleRef.current
        const rawDx = (wp.x - xc) * escala
        const dx0 = espelharCaminhoRef.current ? -rawDx : rawDx
        const dy0 = (wp.y - yc) * escala
        // Rotacao manual da passarela em torno do ponto atual (xc,yc) - o ideal seria
        // sair alinhada em angulo 0 (mesma logica do Player360.jsx: trajetoria e foto
        // vem da mesma gravacao), mas na pratica as fotos extraidas pelo gerar_quadros.py
        // podem nao preservar a mesma referencia de "frente" que o video original tinha,
        // entao expomos esse ajuste manual em vez de assumir alpha=0 como o Player360.
        const rad = THREE.MathUtils.degToRad(ribbonRotationRef.current)
        const dx = dx0 * Math.cos(rad) - dy0 * Math.sin(rad)
        const dy = dx0 * Math.sin(rad) + dy0 * Math.cos(rad)
        const rx = dx
        const rz = -dy
        const ry = -2.1
        return new THREE.Vector3(rx, ry, rz)
      })

      const vertices = []
      const halfWidth = 0.18 * lineThicknessRef.current
      for (let i = 0; i < pathPoints.length; i++) {
        const p = pathPoints[i]
        const dir = new THREE.Vector3()
        if (i < pathPoints.length - 1) dir.subVectors(pathPoints[i + 1], p)
        else dir.subVectors(p, pathPoints[i - 1])
        const len = Math.sqrt(dir.x * dir.x + dir.z * dir.z)
        let px = 0, pz = 1
        if (len > 0) { px = -dir.z / len; pz = dir.x / len }
        vertices.push(p.x - px * halfWidth, p.y, p.z - pz * halfWidth)
        vertices.push(p.x + px * halfWidth, p.y, p.z + pz * halfWidth)
      }
      const indices = []
      for (let i = 0; i < pathPoints.length - 1; i++) {
        const v0 = 2 * i, v1 = 2 * i + 1, v2 = 2 * (i + 1), v3 = 2 * (i + 1) + 1
        indices.push(v0, v1, v2)
        indices.push(v1, v3, v2)
      }
      const geo = line3D.geometry
      geo.setAttribute('position', new THREE.Float32BufferAttribute(vertices, 3))
      geo.setIndex(indices)
      geo.computeVertexNormals()
      geo.attributes.position.needsUpdate = true
      if (geo.index) geo.index.needsUpdate = true
    }

    // --- Avanço automático (play) + loop de render ---
    let ultimoFrameMs = performance.now()
    let raf = null
    const animar = () => {
      const agora = performance.now()
      const dt = (agora - ultimoFrameMs) / 1000
      ultimoFrameMs = agora

      if (!fp.paused()) {
        fp.currentTime(fp.currentTime() + dt * fp.playbackRate())
        if (fp.currentTime() >= fp.duration()) fp.pause()
      }

      const latClamped = Math.max(-85, Math.min(85, lat))
      const phi = THREE.MathUtils.degToRad(90 - latClamped)
      // -90 no theta: com lon=0 (posicao inicial, sem arrastar o mouse), essa
      // formula sozinha aponta a camera para +X (dir=(1,0,0)), mas o cone de FOV
      // no PlantaViewer.jsx (getCameraYaw -> atan2(dir.x, -dir.z)) assume que
      // yaw=0 e' a direcao -Z. Sem esse ajuste, TODA vistoria com PanoramaViewer
      // (fotos) tem o cone desenhado 90 graus girado em relacao ao que a camera
      // realmente mostra - era exatamente o defasamento que o Pedro notou. O
      // Player360.jsx antigo (video.js/videojs-vr) nao tinha esse bug porque o
      // getCameraYaw foi calibrado contra a convencao INTERNA do plugin de VR
      // dele, que ja usava -Z como padrao.
      const theta = THREE.MathUtils.degToRad(lon - 90)
      const target = new THREE.Vector3(
        500 * Math.sin(phi) * Math.cos(theta),
        500 * Math.cos(phi),
        500 * Math.sin(phi) * Math.sin(theta)
      )
      camera.position.set(0, 0, 0)
      camera.lookAt(target)

      try { atualizarPassarela() } catch (e) { /* ignora erros pontuais, nao trava o loop */ }

      renderer.render(scene, camera)
      raf = requestAnimationFrame(animar)
    }
    raf = requestAnimationFrame(animar)

    // --- Resize ---
    const ro = new ResizeObserver(() => {
      const w = container.clientWidth, h = container.clientHeight
      if (w === 0 || h === 0) return
      renderer.setSize(w, h)
      camera.aspect = w / h
      camera.updateProjectionMatrix()
    })
    ro.observe(container)

    if (autoplay) fp.play()
    if (onReady) onReady(fp)

    return () => {
      active = false
      cancelAnimationFrame(raf)
      ro.disconnect()
      renderer.domElement.removeEventListener('pointerdown', onPointerDown)
      window.removeEventListener('pointermove', onPointerMove)
      window.removeEventListener('pointerup', onPointerUp)
      renderer.domElement.removeEventListener('wheel', onWheel)
      fp.off('timeupdate', aoMudarTempo)
      cacheTexturas.forEach((tex) => tex.dispose())
      geometry.dispose()
      matA.dispose()
      matB.dispose()
      renderer.dispose()
      if (container.contains(renderer.domElement)) container.removeChild(renderer.domElement)
      fakePlayerRef.current = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [quadros, manifestUrl, autoplay])

  if (!manifestUrl) {
    return (
      <div className="flex items-center justify-center w-full h-full bg-concreto-900 rounded-lg text-aco-400 text-sm font-mono">
        Nenhuma vistoria em panoramas disponível
      </div>
    )
  }

  if (erro) {
    return (
      <div className="flex items-center justify-center w-full h-full bg-concreto-900 rounded-lg text-alerta text-sm font-mono px-4 text-center">
        Falha ao carregar panoramas: {erro}
      </div>
    )
  }

  if (!quadros) {
    return (
      <div className="flex items-center justify-center w-full h-full bg-concreto-900 rounded-lg text-aco-400 text-sm font-mono">
        Carregando panoramas…
      </div>
    )
  }

  if (quadros.length === 0) {
    return (
      <div className="flex items-center justify-center w-full h-full bg-concreto-900 rounded-lg text-aco-400 text-sm font-mono">
        Nenhum quadro encontrado no manifest desta vistoria
      </div>
    )
  }

  return (
    <div
      ref={containerRef}
      className="w-full h-full rounded-lg overflow-hidden bg-black cursor-grab active:cursor-grabbing"
    />
  )
}
