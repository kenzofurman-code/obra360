// src/lib/visitas.js
import {
  collection, doc, getDocs, getDoc,
  addDoc, updateDoc, deleteDoc,
  query, orderBy, serverTimestamp
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

// ── Escrita ──────────────────────────────────────────────────────────────────

export async function criarVisita({ pavimento, hls_url, thumbnail_url, planta_url, duracao_segundos, waypoints = [], is_imported = false }) {
  const ref = await addDoc(collection(db, COL), {
    pavimento,
    hls_url,                          // URL do index.m3u8 no R2
    thumbnail_url: thumbnail_url || null, // URL de imagem preview (opcional)
    planta_url: planta_url || null,
    duracao_segundos: duracao_segundos || 0,
    heading_offset: 0,                // Azimute/Norte calibrado
    ancora1: null,                    // Ponto de referência 1 {x, y}
    ancora2: null,                    // Ponto de referência 2 {x, y}
    waypoints: waypoints || [],
    is_imported: is_imported || false,
    path_scale: 0.15,                 // Escala da trajetória relativa na planta
    status: 'ready',
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
