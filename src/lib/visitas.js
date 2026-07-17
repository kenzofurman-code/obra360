// src/lib/visitas.js
import {
  collection, doc, getDocs, getDoc,
  addDoc, updateDoc, deleteDoc,
  query, where, orderBy, serverTimestamp
} from 'firebase/firestore'
import { db } from './firebase'

const COL = 'visitas'

// ── Leitura ─────────────────────────────────────────────────────────────────

export async function listarVisitas() {
  const q = query(collection(db, COL), orderBy('data', 'desc'))
  const snap = await getDocs(q)
  return snap.docs.map(d => ({ id: d.id, ...d.data() }))
}

export async function getVisita(id) {
  const snap = await getDoc(doc(db, COL, id))
  if (!snap.exists()) return null
  return { id: snap.id, ...snap.data() }
}

// Historico de vistorias de UM local, mais recente primeiro (por data_vistoria,
// nao pelo timestamp de upload) - base do drawer de historico/comparacao do
// item 4.5. Ordena em memoria (nao no Firestore) pra nao exigir um indice
// composto (where + orderBy em campos diferentes precisaria de indice manual).
export async function listarVisitasDoLocal(localId) {
  const q = query(collection(db, COL), where('local_id', '==', localId))
  const snap = await getDocs(q)
  const visitas = snap.docs.map(d => ({ id: d.id, ...d.data() }))
  return visitas.sort((a, b) => {
    const da = a.data_vistoria?.toDate?.() ?? new Date(a.data_vistoria ?? 0)
    const db_ = b.data_vistoria?.toDate?.() ?? new Date(b.data_vistoria ?? 0)
    return db_ - da
  })
}

// ── Escrita ──────────────────────────────────────────────────────────────────

// Valores padrao de calibracao abaixo (heading/escala/espelhar/passarela/cone)
// vieram da aproximacao validada pelo Pedro em 2026-07-15 rodando o SLAM real
// (Insta360 X3 + stella_vslam via Docker) em duas importacoes diferentes (P073
// e P070) - os mesmos ajustes se repetiram nas duas, entao viram o ponto de
// partida de toda vistoria nova em vez de precisar recalibrar do zero cada
// vez (motivo: 1 rodada de worker.py leva varios minutos, refazer manualmente
// pra cada vistoria nao escala). Se ISSO deixar de valer (ex.: mudar de
// camera, resolucao, ou orientacao de gravacao), reajuste os padroes aqui.
//
// heading_offset/path_scale/espelhar_caminho: os 3 que realmente entram no
// alinhamento com a planta (run_map_matching em processar_vistoria.py) -
// comecar com eles proximos do certo e' o que destrava a deteccao de
// cruzamento de portas (min_portas=4 pra auto-calibracao por Umeyama entrar).
// NOTA: o comentario antigo dizia que espelhar_caminho=False era o padrao
// "correto" pos-fix do SLAM (ver tum_para_raw_waypoints) - a calibracao real
// do Pedro mostrou que True da o melhor resultado visual pra essa camera/
// configuracao de gravacao, entao virou o novo padrao. Se voltar pra H.264/
// outra camera e sair espelhado ao contrario, e' aqui que se ajusta de volta.
//
// passarela_escala/passarela_rotacao/cone_frame_offset: SO cosmeticos (como a
// passarela 3D e o cone de direcao aparecem desenhados em cima da foto 360
// no visualizador) - nao afetam o alinhamento com a planta nem a deteccao de
// portas, mas repetiram tambem nas duas calibracoes entao ja vao default.
export async function criarVisita({
  pavimento, hls_url, thumbnail_url, planta_url, duracao_segundos,
  waypoints = [], is_imported = false,
  ancora1 = null, ancora2 = null, // opcional: definida na hora do upload (ver Upload.jsx)
  heading_offset = 90, path_scale = 0.029, espelhar_caminho = true,
  passarela_escala = 0.08, passarela_rotacao = 90, cone_frame_offset = 90,
  // obra_id/local_id: novos campos (2026-07-14) - ligam a vistoria ao seletor
  // de projeto/aba de locais (ver obras.js/locais.js). data_vistoria e' a data
  // REAL da inspecao (editavel no Upload.jsx, pode diferir da data de upload
  // guardada em `data` abaixo via serverTimestamp) - e' o campo usado pra
  // ordenar o historico de um local (ver listarVisitasDoLocal) no futuro
  // drawer de historico/comparacao (item 4.5). Ambos opcionais por enquanto
  // pra nao quebrar vistorias antigas criadas antes dessa mudanca.
  obra_id = null, local_id = null, data_vistoria = null,
  // Fluxo novo (2026-07-17, fim do Cloudflare Stream): video bruto no R2 +
  // fila do worker na VPS. video_r2_key aponta pro objeto no bucket;
  // status 'na_fila' faz o worker.py --poll pegar a vistoria sozinho.
  video_r2_key = null, status = 'ready',
}) {
  const ref = await addDoc(collection(db, COL), {
    pavimento,
    obra_id,
    local_id,
    data_vistoria: data_vistoria || new Date(),
    hls_url,                          // URL do index.m3u8 no R2
    thumbnail_url: thumbnail_url || null, // URL de imagem preview (opcional)
    planta_url: planta_url || null,
    duracao_segundos: duracao_segundos || 0,
    heading_offset,                   // Azimute/Norte calibrado
    ancora1,                          // Ponto de referência 1 {x, y}
    ancora2,                          // Ponto de referência 2 {x, y}
    waypoints: waypoints || [],
    is_imported: is_imported || false,
    path_scale,                       // Escala da trajetória relativa na planta
    espelhar_caminho,
    passarela_escala,                 // Escala visual da passarela 3D (cosmetico)
    passarela_rotacao,                // Rotacao visual da passarela 3D (cosmetico)
    cone_frame_offset,                // Ajuste visual do cone de direcao (cosmetico)
    video_r2_key,
    status,
    data: serverTimestamp(),
  })
  return ref.id
}

export async function salvarWaypoints(visitaId, waypoints) {
  // waypoints: [{ t, x, y, label, observacao }]
  await updateDoc(doc(db, COL, visitaId), { waypoints })
}

export async function atualizarVisita(visitaId, dados) {
  // dados: { heading_offset, ancora1, ancora2, pavimento, etc. }
  await updateDoc(doc(db, COL, visitaId), dados)
}

export async function atualizarStatus(visitaId, status) {
  await updateDoc(doc(db, COL, visitaId), { status })
}

export async function deletarVisita(visitaId) {
  await deleteDoc(doc(db, COL, visitaId))
}

// Exclusao COMPLETA (2026-07-17): alem do doc do Firestore, limpa o storage
// R2 da vistoria (panoramas/manifest/waypoints/mapa.msg sob o prefixo
// {visita_id}/ + o video bruto em video_r2_key) via api_medicao.py na VPS -
// as credenciais do R2 nunca chegam ao navegador. Sem essa limpeza, cada
// exclusao deixava ate ~50GB orfaos no bucket.
// Se apiUrl nao estiver configurada, lanca erro claro em vez de excluir pela
// metade em silencio - o chamador (Home.jsx) oferece o fallback explicito de
// excluir so o registro.
export async function excluirVisitaCompleta(visita, { apiUrl, apiKey } = {}) {
  if (!apiUrl) {
    throw new Error('API de storage não configurada (VITE_API_MEDICAO_URL) - ' +
      'não dá pra limpar os arquivos da vistoria no armazenamento.')
  }
  const headers = { 'Content-Type': 'application/json' }
  if (apiKey) headers['X-Api-Key'] = apiKey
  const r = await fetch(`${apiUrl}/vistoria/excluir-storage`, {
    method: 'POST', headers,
    body: JSON.stringify({
      visita_id: visita.id,
      video_r2_key: visita.video_r2_key || null,
    }),
  })
  const json = await r.json().catch(() => ({}))
  if (!r.ok || json.erro) {
    throw new Error(json.erro || `Falha na limpeza do storage (HTTP ${r.status})`)
  }
  await deleteDoc(doc(db, COL, visita.id))
  return json // { objetos_removidos, video_removido }
}
