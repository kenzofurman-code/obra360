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
// Yaw (rotacao em torno do eixo vertical Y) da CAMERA no mundo, a partir do
// quaternion pose_raw.quat_wc [x,y,z,w] (camera->mundo, convencao scipy). A
// "frente" da foto equiretangular e' +Z no frame da camera (ver raio_do_clique
// em medir_panorama.py: u=0.5,v=0.5 -> dir=(0,0,1)). Rotaciona (0,0,1) pelo
// quaternion e mede o yaw no plano XZ. Usado pra sincronizar o cone/visao com a
// orientacao real da camera em cada frame (opcao 2, 2026-07-22).
export function yawMundoDaPose(q) {
  if (!q || q.length < 4) return null
  const [x, y, z, w] = q
  const fx = 2 * (x * z + w * y)          // componente X do +Z rotacionado
  const fz = 1 - 2 * (x * x + y * y)      // componente Z do +Z rotacionado
  return Math.atan2(fx, fz)               // radianos
}

// Convencao do yaw do frame (three.js vs SLAM), calibrada no navegador
// 2026-07-22: o SLAM usa eixo vertical invertido -> sinal -1; o offset alinha
// o frame 0 (inicio da vistoria, onde o drift do SLAM e' ~zero) com a planta.
// Resíduo remanescente ao longo do tour = deriva do SLAM (nao ha' offset unico
// perfeito numa trajetoria com drift).
const SINAL_FRAME_YAW = -1
const OFFSET_FRAME_YAW_GRAUS = 38.1

class FakePlayer {
  constructor() {
    this._listeners = {}
    this._time = 0
    this._duration = 0
    this._paused = true
    this._rate = 1
    this.camera = null
    this.frameYaw = 0  // yaw do mundo da foto atual (rad) - ver yawMundoDaPose
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
    return { camera: this.camera, frameYaw: this.frameYaw }
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
  onQuadros, // expõe a lista de quadros (fotos) carregada do manifest pro componente pai
             // (Visita.jsx), que precisa dela pra desenhar os marcadores de foto na
             // PlantaViewer.jsx - ver comentário em "3.5 Desenha marcadores dos frames"
             // lá e no useMemo framesAlinhados em Visita.jsx
  autoplay = false,
  waypoints = [],
  headingOffset = 0,
  lineOpacity = 80,
  lineThickness = 1.0,
  espelharCaminho = false,
  ribbonScale = 1.0, // multiplica o fator de escala base (22) da passarela 3D
  ribbonRotationOffset = 0, // graus - gira a passarela 3D em torno do ponto atual antes de desenhar
  // --- Medição (feature nova, 2026-07-16 - ver api_medicao.py) ---
  modoMedicao = false, // true = clique na foto marca pontos de medição em vez de nada
  modoCalibrar = false, // true = os 2 próximos pontos calibram a escala (precisa larguraCalibracaoM), em vez de medir direto
  mapaUrl = null, // visita.mapa_url (mapa.msg no R2) - sem isso, medição fica indisponível
  apiMedicaoUrl = null, // base da API (ver api_medicao.py) - ex.: https://api.obra360.exemplo
  apiMedicaoKey = null, // valor de MEDICAO_API_KEY da API (header X-Api-Key); null = sem auth
  escalaSlamMetros = null, // visita.escala_slam_metros, se já calibrada (senão /medir devolve só unid. SLAM)
  larguraCalibracaoM = null, // largura real (m) do vão usado pra calibrar, quando modoCalibrar=true
  onResultadoMedicao, // (resultado, { calibrando }) => void - chamado com a resposta da API
  onErroMedicao, // (mensagem) => void - erro local (sem pose_raw, sem mapaUrl, etc.) antes mesmo de chamar a API
  mostrarLandmarks = false, // true = desenha os landmarks do mapa sobre a foto (guia de onde da' pra medir; usa /landmarks_frame)
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
  const modoMedicaoRef = useRef(modoMedicao)
  const modoCalibrarRef = useRef(modoCalibrar)
  const mapaUrlRef = useRef(mapaUrl)
  const apiMedicaoUrlRef = useRef(apiMedicaoUrl)
  const apiMedicaoKeyRef = useRef(apiMedicaoKey)
  const escalaSlamMetrosRef = useRef(escalaSlamMetros)
  const larguraCalibracaoMRef = useRef(larguraCalibracaoM)
  const onResultadoMedicaoRef = useRef(onResultadoMedicao)
  const onErroMedicaoRef = useRef(onErroMedicao)
  const mostrarLandmarksRef = useRef(mostrarLandmarks)
  // atribuida pelo effect de cena - refaz o overlay de landmarks do quadro atual
  // (ao ligar o toggle sem trocar de foto). Ver mostrarLandmarks/atualizarLandmarks.
  const atualizarLandmarksRef = useRef(() => {})
  // atribuída pelo effect de cena (abaixo) - permite limpar os marcadores/pontos
  // de medição em andamento sempre que o modo muda, SEM precisar recriar a cena
  // inteira (o effect de cena só depende de quadros/manifestUrl/autoplay).
  const limparMedicaoRef = useRef(() => {})
  useEffect(() => { waypointsRef.current = waypoints }, [waypoints])
  useEffect(() => { lineOpacityRef.current = lineOpacity }, [lineOpacity])
  useEffect(() => { lineThicknessRef.current = lineThickness }, [lineThickness])
  useEffect(() => { espelharCaminhoRef.current = espelharCaminho }, [espelharCaminho])
  useEffect(() => { ribbonScaleRef.current = ribbonScale }, [ribbonScale])
  useEffect(() => { ribbonRotationRef.current = ribbonRotationOffset }, [ribbonRotationOffset])
  useEffect(() => { mapaUrlRef.current = mapaUrl }, [mapaUrl])
  useEffect(() => { apiMedicaoUrlRef.current = apiMedicaoUrl }, [apiMedicaoUrl])
  useEffect(() => { apiMedicaoKeyRef.current = apiMedicaoKey }, [apiMedicaoKey])
  useEffect(() => { escalaSlamMetrosRef.current = escalaSlamMetros }, [escalaSlamMetros])
  useEffect(() => { larguraCalibracaoMRef.current = larguraCalibracaoM }, [larguraCalibracaoM])
  useEffect(() => { onResultadoMedicaoRef.current = onResultadoMedicao }, [onResultadoMedicao])
  useEffect(() => { onErroMedicaoRef.current = onErroMedicao }, [onErroMedicao])
  useEffect(() => {
    modoMedicaoRef.current = modoMedicao
    limparMedicaoRef.current()
  }, [modoMedicao])
  useEffect(() => {
    modoCalibrarRef.current = modoCalibrar
    limparMedicaoRef.current()
  }, [modoCalibrar])
  useEffect(() => {
    mostrarLandmarksRef.current = mostrarLandmarks
    atualizarLandmarksRef.current()  // liga/desliga o overlay no quadro atual
  }, [mostrarLandmarks])
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
        if (onQuadros) onQuadros(lista)
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

    // --- Overlay de landmarks (frente 1, guia de clique) ---
    // Nuvem de pontos do mapa reprojetada na foto (endpoint /landmarks_frame).
    // Mostra onde HA' pontos 3D densos (bom pra medir) vs areas lisas (sem ponto).
    // Pontos num raio menor (470 < 500 da esfera) pra ficarem na frente da textura.
    const landmarksGeo = new THREE.BufferGeometry()
    landmarksGeo.setAttribute('position', new THREE.Float32BufferAttribute([], 3))
    const landmarksMat = new THREE.PointsMaterial({
      color: 0x22d3ee, size: 4, sizeAttenuation: false,
      transparent: true, opacity: 0.85, depthTest: false,
    })
    const landmarksObj = new THREE.Points(landmarksGeo, landmarksMat)
    landmarksObj.renderOrder = 2 // sempre por cima da foto e da fita
    landmarksObj.visible = false
    landmarksObj.frustumCulled = false
    scene.add(landmarksObj)

    // (u,v) equiretangular -> ponto 3D na esfera (INVERSO exato de pegarUVDoClique,
    // pra os pontos cairem exatamente onde os cliques de medicao caem). r=470.
    const uvParaPonto = (u, v, r = 470) => {
      const lon = 2 * Math.PI * u
      const sv = Math.sin(Math.PI * v)
      return [r * Math.cos(lon) * sv, -r * Math.cos(Math.PI * v), r * Math.sin(lon) * sv]
    }

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

      // Opcao 2 (2026-07-22): orientacao world-consistent. Cada foto tem uma
      // orientacao de camera propria (pose_raw.quat_wc). Sem isso, lon=0 mostra
      // sempre a "frente da foto" - que aponta pra direcoes diferentes do mundo
      // a cada frame, entao o cone (que le a direcao da camera) dessincroniza.
      // Aqui: (1) publico o yaw do mundo da foto em fp.frameYaw pro cone somar;
      // (2) ajusto lon pela diferenca de yaw entre a foto anterior e a nova, pra
      // a VISAO continuar apontando pra mesma direcao do mundo (sem "pulo").
      // Publica o yaw do mundo da foto pro cone da planta sincronizar (opcao 2).
      // NAO ajusta lon/camera aqui: mexer na camera na troca de frame fazia a
      // fita do caminho (geometria fixa na cena) "andar" na tela - efeito
      // colateral removido 2026-07-22 a pedido do Pedro. A foto e a fita ficam
      // paradas; so' o cone gira acompanhando a orientacao real do frame.
      const yawNovo = yawMundoDaPose(quadro?.pose_raw?.quat_wc)
      if (yawNovo !== null) {
        fp.frameYaw = SINAL_FRAME_YAW * yawNovo + OFFSET_FRAME_YAW_GRAUS * Math.PI / 180
      }

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

      atualizarLandmarks() // refaz o overlay de landmarks pro novo quadro (se ligado)
    }

    // Overlay de landmarks (frente 1): busca /landmarks_frame do quadro atual e
    // desenha os pontos na foto. So' roda com o toggle ligado + mapaUrl/apiUrl.
    let landmarksReqId = 0
    const atualizarLandmarks = () => {
      const q = quadrosRef.current[indiceAtualExibido]
      const apiUrl = apiMedicaoUrlRef.current
      const mapa = mapaUrlRef.current
      if (!mostrarLandmarksRef.current || !apiUrl || !mapa || !q || q.t == null) {
        landmarksObj.visible = false
        return
      }
      const reqId = ++landmarksReqId
      const headers = { 'Content-Type': 'application/json' }
      if (apiMedicaoKeyRef.current) headers['X-Api-Key'] = apiMedicaoKeyRef.current
      fetch(`${apiUrl}/landmarks_frame`, {
        method: 'POST', headers,
        body: JSON.stringify({ mapa_url: mapa, t: q.t }),
      })
        .then((r) => r.json())
        .then((json) => {
          if (!active || reqId !== landmarksReqId || !mostrarLandmarksRef.current) return
          const pts = json?.pontos || []
          const pos = new Float32Array(pts.length * 3)
          for (let i = 0; i < pts.length; i++) {
            const [x, y, z] = uvParaPonto(pts[i].u, pts[i].v)
            pos[i * 3] = x; pos[i * 3 + 1] = y; pos[i * 3 + 2] = z
          }
          landmarksGeo.setAttribute('position', new THREE.BufferAttribute(pos, 3))
          landmarksGeo.attributes.position.needsUpdate = true
          landmarksGeo.computeBoundingSphere()
          landmarksObj.visible = true
        })
        .catch(() => { /* silencioso - overlay e' so' um guia visual */ })
    }
    atualizarLandmarksRef.current = atualizarLandmarks

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
    // distancia total percorrida durante o gesto - separa "arrastar pra olhar ao redor"
    // de "clique" (parado ou quase parado), igual ao mesmo padrao ja usado em
    // PlantaViewer.jsx (totalDragDistRef) pro clique-no-trajeto.
    let totalDragDist = 0

    const onPointerDown = (e) => {
      isUserInteracting = true
      onDownMouseX = e.clientX
      onDownMouseY = e.clientY
      onDownLon = lon
      onDownLat = lat
      totalDragDist = 0
    }
    const onPointerMove = (e) => {
      // guarda a posicao do mouse SEMPRE (mesmo sem estar arrastando) pro hover da
      // fita abaixo (ver hoverFrameAtual/atualizarHoverFrame) - so' o giro da camera
      // (lon/lat) depende de isUserInteracting.
      mouseClientX = e.clientX
      mouseClientY = e.clientY
      if (!isUserInteracting) return
      totalDragDist = Math.max(totalDragDist, Math.hypot(e.clientX - onDownMouseX, e.clientY - onDownMouseY))
      lon = (onDownMouseX - e.clientX) * 0.15 + onDownLon
      lat = (e.clientY - onDownMouseY) * 0.15 + onDownLat
    }
    const onPointerUp = () => {
      isUserInteracting = false
      // Modo medição (ver bloco "Medição" abaixo) tem prioridade e é mutuamente
      // exclusivo com o pulo de frame por clique na fita - clicar medindo não
      // deve também pular de foto.
      if (modoMedicaoRef.current || modoCalibrarRef.current) {
        if (totalDragDist < 6) tentarClicarMedicao(mouseClientX, mouseClientY)
        return
      }
      // Clique (nao arrasto) sobre a fita com uma foto em destaque (ver bolinha azul-
      // clara do hover) - pedido do Pedro em 2026-07-15: poder clicar na fita e pular
      // direto pra aquele frame, igual ja funciona na planta baixa.
      if (totalDragDist < 6 && hoverFrameAtual) {
        fp.currentTime(hoverFrameAtual.t)
      }
    }
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
    // Marcador de foto sob o mouse: pedido do Pedro em 2026-07-15 - a 1ª versão
    // desenhava TODAS as fotos na fita o tempo todo (poluía a foto 360°); a versão
    // atual só destaca a foto mais próxima de onde o mouse está passando sobre a
    // fita (raycasting contra line3D), dando noção de onde estão/distância entre
    // elas sem cobrir a imagem, e permite clicar (ver onPointerUp acima) pra pular
    // direto pra aquele frame.
    let hoverMesh = null
    let hoverFrameAtual = null // { t, x, y, ... } do quadro mais proximo do mouse, ou null
    const raycasterFita = new THREE.Raycaster()
    let mouseClientX = null, mouseClientY = null

    // --- Medição (feature nova 2026-07-16 - ver api_medicao.py) ---
    // Clique de 2 pontos sobre a ESFERA do panorama (não a fita) quando
    // modoMedicao/modoCalibrar estiver ativo (ver onPointerUp acima, que chama
    // tentarClicarMedicao). Cada ponto vira {u, v, pos_w, quat_wc} - a pose
    // vem do proprio quadro em exibição no momento do clique (mesmo pose_raw
    // que gerar_quadros.py anexa ao manifest.json quando --traj-completa foi
    // passado - ver item 13 do CLAUDE.md). Os 2 pontos juntos disparam um
    // POST pra api_medicao.py (/medir ou /calibrar).
    const raycasterMedicao = new THREE.Raycaster()
    let pontosMedicao = [] // até 2: { u, v, pos_w, quat_wc }
    let marcadoresMedicao = [] // THREE.Mesh - um por ponto já clicado

    const limparPontosMedicao = () => {
      marcadoresMedicao.forEach((m) => {
        scene.remove(m)
        m.geometry.dispose()
        m.material.dispose()
      })
      marcadoresMedicao = []
      pontosMedicao = []
    }
    limparMedicaoRef.current = limparPontosMedicao

    // Converte um clique de tela em (u,v) da textura equiretangular via
    // raycast contra a esfera VISÍVEL no momento (frenteEhA decide qual das
    // duas, igual ao resto do double-buffer). Usa hits[0].uv (calculado pelo
    // próprio three.js) em vez de recalcular lon/lat manualmente - garante
    // bater com o que está literalmente desenhado na tela.
    const pegarUVDoClique = (clientX, clientY) => {
      if (clientX === null || clientY === null) return null
      const rect = renderer.domElement.getBoundingClientRect()
      const ndcX = ((clientX - rect.left) / rect.width) * 2 - 1
      const ndcY = -(((clientY - rect.top) / rect.height) * 2 - 1)
      if (ndcX < -1 || ndcX > 1 || ndcY < -1 || ndcY > 1) return null
      raycasterMedicao.setFromCamera({ x: ndcX, y: ndcY }, camera)
      const meshVisivel = frenteEhA ? sphereA : sphereB
      const hits = raycasterMedicao.intersectObject(meshVisivel)
      if (hits.length === 0 || !hits[0].uv) return null
      // Convencao do UV (corrigida 2026-07-22): a API/medir_panorama.py usa
      // equiretangular u=0..1 esquerda->direita, v=0..1 TOPO->baixo. O
      // SphereGeometry do three.js poe uv.y=1 no TOPO (uv.y=0 embaixo) - e o
      // scale(-1,1,1) inverte a esfera no MUNDO mas NAO mexe no UV da
      // geometria. Entao: u = uv.x (ok), v = 1 - uv.y (INVERTE - senao a
      // medicao saia sempre na altura espelhada, causa provavel de "medicao
      // muito ruim"). Confirmar com o overlay de landmarks (endpoint
      // /landmarks_frame) - os pontos tem que cair nas features reais.
      return { u: hits[0].uv.x, v: 1 - hits[0].uv.y, point: hits[0].point.clone() }
    }

    const adicionarMarcadorMedicao = (point, cor) => {
      const geo = new THREE.SphereGeometry(3, 16, 16)
      const mat = new THREE.MeshBasicMaterial({
        color: cor, transparent: true, opacity: 0.9, depthWrite: false,
      })
      const mesh = new THREE.Mesh(geo, mat)
      mesh.position.copy(point)
      mesh.renderOrder = 3
      mesh.frustumCulled = false
      scene.add(mesh)
      marcadoresMedicao.push(mesh)
    }

    // Chamado pelo onPointerUp quando modoMedicao/modoCalibrar está ativo e o
    // gesto foi um clique (não arrasto). Acumula até 2 pontos; ao chegar no
    // 2º, dispara a chamada pra api_medicao.py e limpa pra próxima medição.
    const tentarClicarMedicao = (clientX, clientY) => {
      const quadroAtual = quadrosRef.current[indiceAtualExibido]
      // Fix 2026-07-17 (item 21 do CLAUDE.md): o campo essencial agora é o
      // "t" do quadro - a API deriva a pose dos keyframes do próprio
      // mapa.msg (referencial certo). pose_raw vira só fallback legado
      // (está no referencial da trajetória, diferente do mapa).
      if (!quadroAtual || (quadroAtual.t == null && !quadroAtual.pose_raw)) {
        if (onErroMedicaoRef.current) {
          onErroMedicaoRef.current(
            'Esta foto não tem tempo/pose 3D (vistoria processada sem SLAM) - ' +
            'medição indisponível para ela. Tente noutra foto da mesma vistoria.'
          )
        }
        return
      }
      const hit = pegarUVDoClique(clientX, clientY)
      if (!hit) return
      adicionarMarcadorMedicao(hit.point, pontosMedicao.length === 0 ? 0xfbbf24 : 0x22d3ee)
      const ponto = { u: hit.u, v: hit.v }
      if (quadroAtual.t != null) ponto.t = quadroAtual.t
      else {
        // fallback legado (referencial da trajetória - pode sair deslocado)
        ponto.pos_w = quadroAtual.pose_raw.pos_w
        ponto.quat_wc = quadroAtual.pose_raw.quat_wc
      }
      pontosMedicao.push(ponto)

      if (pontosMedicao.length < 2) return

      const pontosParaEnviar = pontosMedicao
      const calibrando = modoCalibrarRef.current
      limparPontosMedicao() // já reseta pro próximo par, mesmo antes da resposta da API chegar

      const apiUrl = apiMedicaoUrlRef.current
      const mapaUrlAtual = mapaUrlRef.current
      if (!apiUrl || !mapaUrlAtual) {
        if (onErroMedicaoRef.current) {
          onErroMedicaoRef.current(
            !mapaUrlAtual
              ? 'Esta vistoria não tem mapa 3D disponível (mapa_url) - processada ' +
                'antes dessa feature existir, ou o SLAM não rodou/não subiu o mapa ' +
                'pro R2.'
              : 'API de medição não configurada (apiMedicaoUrl).'
          )
        }
        return
      }
      if (calibrando && !larguraCalibracaoMRef.current) {
        if (onErroMedicaoRef.current) {
          onErroMedicaoRef.current('Informe a largura real (m) antes de calibrar.')
        }
        return
      }

      const corpo = { mapa_url: mapaUrlAtual, pontos: pontosParaEnviar }
      if (calibrando) {
        corpo.largura_real_m = larguraCalibracaoMRef.current
      } else if (escalaSlamMetrosRef.current) {
        corpo.escala_slam_metros = escalaSlamMetrosRef.current
      }

      const headers = { 'Content-Type': 'application/json' }
      if (apiMedicaoKeyRef.current) headers['X-Api-Key'] = apiMedicaoKeyRef.current
      fetch(`${apiUrl}/${calibrando ? 'calibrar' : 'medir'}`, {
        method: 'POST',
        headers,
        body: JSON.stringify(corpo),
      })
        .then((r) => r.json())
        .then((json) => {
          if (onResultadoMedicaoRef.current) onResultadoMedicaoRef.current(json, { calibrando })
        })
        .catch((e) => {
          if (onErroMedicaoRef.current) {
            onErroMedicaoRef.current(`Falha ao chamar a API de medição: ${e.message || e}`)
          }
        })
    }

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

    // Acha a foto mais proxima de onde o mouse esta passando sobre a fita (raycast
    // contra o MESH da fita, nao contra um ponto por foto) - so' roda quando ha'
    // posicao de mouse conhecida, chamado a cada frame do loop de render (ver
    // animar() abaixo) depois de atualizarPassarela() ter deixado line3D atualizado.
    const atualizarHoverFrame = () => {
      if (!line3D || mouseClientX === null || quadrosRef.current.length === 0) {
        hoverFrameAtual = null
        if (hoverMesh) hoverMesh.visible = false
        return
      }
      const rect = renderer.domElement.getBoundingClientRect()
      const ndcX = ((mouseClientX - rect.left) / rect.width) * 2 - 1
      const ndcY = -(((mouseClientY - rect.top) / rect.height) * 2 - 1)
      if (ndcX < -1 || ndcX > 1 || ndcY < -1 || ndcY > 1) {
        hoverFrameAtual = null
        if (hoverMesh) hoverMesh.visible = false
        return
      }
      raycasterFita.setFromCamera({ x: ndcX, y: ndcY }, camera)
      const hits = raycasterFita.intersectObject(line3D)
      if (hits.length === 0) {
        hoverFrameAtual = null
        if (hoverMesh) hoverMesh.visible = false
        return
      }
      const hitPoint = hits[0].point
      const xc = renderPos.x
      const yc = renderPos.y
      const escala = 22 * ribbonScaleRef.current
      const rad = THREE.MathUtils.degToRad(ribbonRotationRef.current)
      let melhorDist = Infinity
      let melhorQuadro = null
      let melhorX = 0
      let melhorZ = 0
      for (const q of quadrosRef.current) {
        if (!Number.isFinite(q?.x) || !Number.isFinite(q?.y)) continue
        const rawDx = (q.x - xc) * escala
        const dx0 = espelharCaminhoRef.current ? -rawDx : rawDx
        const dy0 = (q.y - yc) * escala
        const dx = dx0 * Math.cos(rad) - dy0 * Math.sin(rad)
        const dy = dx0 * Math.sin(rad) + dy0 * Math.cos(rad)
        const rz = -dy
        const dist = Math.hypot(dx - hitPoint.x, rz - hitPoint.z)
        if (dist < melhorDist) { melhorDist = dist; melhorQuadro = q; melhorX = dx; melhorZ = rz }
      }
      if (!melhorQuadro) {
        hoverFrameAtual = null
        if (hoverMesh) hoverMesh.visible = false
        return
      }
      hoverFrameAtual = melhorQuadro
      if (!hoverMesh) {
        // Esfera unitaria (raio 1) - o tamanho REAL vem do scale abaixo, recalculado
        // a cada frame a partir da espessura atual da fita (lineThicknessRef), pra
        // sempre ficar só um pouco maior que ela (~5%), nunca um tamanho fixo em
        // unidades de cena. Bug corrigido 2026-07-15: raio fixo de 1.3 ficava GIGANTE
        // perto da fita (halfWidth da fita e' só ~0.11 com a espessura padrao de 0.6x
        // - a bolinha saia ~12x maior que a fita, cobrindo a foto toda).
        const geoBolinha = new THREE.SphereGeometry(1, 16, 16)
        const matBolinha = new THREE.MeshBasicMaterial({
          color: 0x93c5fd, // azul mais claro que o 0x3b82f6 da fita, conforme pedido do Pedro
          transparent: true,
          opacity: 0.95,
          depthWrite: false,
        })
        hoverMesh = new THREE.Mesh(geoBolinha, matBolinha)
        hoverMesh.frustumCulled = false
        hoverMesh.renderOrder = 2 // acima da fita (1) e das esferas do panorama (0)
        scene.add(hoverMesh)
      }
      // raio = metade da largura da fita (halfWidth) + 5%, pra bolinha ficar "só um
      // pouco maior que a fita" (pedido do Pedro) em vez de um tamanho fixo.
      const halfWidthAtual = 0.18 * lineThicknessRef.current
      hoverMesh.scale.setScalar(halfWidthAtual * 1.05)
      hoverMesh.visible = true
      hoverMesh.position.set(melhorX, -2.0, melhorZ)
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
      // updateMatrixWorld explicito: o raycaster do hover (abaixo) precisa da matriz
      // da camera ja refletindo o lookAt() acima - sem isso ela so' fica correta
      // depois do proximo renderer.render(), um frame atrasada.
      camera.updateMatrixWorld()

      try { atualizarPassarela() } catch (e) { /* ignora erros pontuais, nao trava o loop */ }
      try { atualizarHoverFrame() } catch (e) { /* ignora erros pontuais, nao trava o loop */ }

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
      landmarksGeo.dispose()
      landmarksMat.dispose()
      atualizarLandmarksRef.current = () => {}
      if (hoverMesh) {
        hoverMesh.geometry.dispose()
        hoverMesh.material.dispose()
      }
      limparPontosMedicao()
      if (limparMedicaoRef.current === limparPontosMedicao) {
        limparMedicaoRef.current = () => {}
      }
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
