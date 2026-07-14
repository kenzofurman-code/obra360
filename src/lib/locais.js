// src/lib/locais.js
// "Local" = um espaço fixo dentro de uma obra (pavimento, apartamento, área
// comum etc.) que acumula VÁRIAS vistorias ao longo do tempo - cada upload
// novo pro mesmo local cria uma visita adicional (ver visitas.js::criarVisita,
// campo local_id + data_vistoria), em vez de sobrescrever a anterior. Isso é
// o que viabiliza o histórico/comparação por data (item 4.5 do roadmap).
//
// planta_url mora aqui (no local), não na visita - a planta baixa de um local
// não muda a cada vistoria nova, só o vídeo/trajetória/panoramas mudam.
import {
  collection, doc, getDocs, getDoc,
  addDoc, updateDoc, deleteDoc,
  query, where, serverTimestamp
} from 'firebase/firestore'
import { db } from './firebase'

const COL = 'locais'

export async function listarLocaisDaObra(obraId) {
  // So' o filtro de igualdade no Firestore (sem orderBy) - where('obra_id','==')
  // + orderBy('criado_em') em campos diferentes exigiria criar um indice
  // composto manualmente no Firebase Console antes de funcionar. Ordena em
  // memoria em vez disso (mesmo truque de listarVisitasDoLocal em visitas.js).
  const q = query(collection(db, COL), where('obra_id', '==', obraId))
  const snap = await getDocs(q)
  const locais = snap.docs.map(d => ({ id: d.id, ...d.data() }))
  return locais.sort((a, b) => {
    const ta = a.criado_em?.toDate?.() ?? new Date(0)
    const tb = b.criado_em?.toDate?.() ?? new Date(0)
    return ta - tb
  })
}

export async function getLocal(id) {
  const snap = await getDoc(doc(db, COL, id))
  if (!snap.exists()) return null
  return { id: snap.id, ...snap.data() }
}

export async function criarLocal({ obra_id, nome, planta_url = null }) {
  const ref = await addDoc(collection(db, COL), {
    obra_id,
    nome,
    planta_url,
    criado_em: serverTimestamp(),
  })
  return ref.id
}

export async function atualizarLocal(localId, dados) {
  await updateDoc(doc(db, COL, localId), dados)
}

export async function deletarLocal(localId) {
  // Nota: nao deleta em cascata as vistorias ligadas a esse local - mesma
  // ressalva de deletarObra() em obras.js.
  await deleteDoc(doc(db, COL, localId))
}
