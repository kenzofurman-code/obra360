// src/lib/obras.js
// "Obra" = projeto/empreendimento (nível mais alto, ex.: "PACE", "GLB Partilha").
// Cada obra tem vários "locais" (ver locais.js) - pavimentos, apartamentos,
// áreas comuns etc. - que por sua vez acumulam várias vistorias ao longo do
// tempo (histórico/comparação, ver item 4.5 do roadmap).
import {
  collection, doc, getDocs, getDoc,
  addDoc, updateDoc, deleteDoc,
  query, orderBy, serverTimestamp
} from 'firebase/firestore'
import { db } from './firebase'

const COL = 'obras'

export async function listarObras() {
  const q = query(collection(db, COL), orderBy('criado_em', 'desc'))
  const snap = await getDocs(q)
  return snap.docs.map(d => ({ id: d.id, ...d.data() }))
}

export async function getObra(id) {
  const snap = await getDoc(doc(db, COL, id))
  if (!snap.exists()) return null
  return { id: snap.id, ...snap.data() }
}

export async function criarObra({ nome, endereco = '', area_m2 = null, thumbnail_url = null }) {
  const ref = await addDoc(collection(db, COL), {
    nome,
    endereco,
    area_m2,
    thumbnail_url,
    criado_em: serverTimestamp(),
  })
  return ref.id
}

export async function atualizarObra(obraId, dados) {
  await updateDoc(doc(db, COL, obraId), dados)
}

export async function deletarObra(obraId) {
  // Nota: nao deleta em cascata locais/visitas ligados a essa obra (ainda nao
  // ha' um passo de limpeza automatica) - cuidado ao remover uma obra com
  // locais/vistorias reais.
  await deleteDoc(doc(db, COL, obraId))
}
