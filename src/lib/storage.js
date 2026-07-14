// src/lib/storage.js
// Helper compartilhado de upload pro Firebase Storage - extraído do Upload.jsx
// (uploadPlanta) pra reaproveitar em Locais.jsx e Obras.jsx sem duplicar a
// lógica (mesmo padrão de nome de arquivo: `${pasta}/${timestamp}_${nome}`).
import { ref, uploadBytes, getDownloadURL } from 'firebase/storage'
import { storage } from './firebase'

export async function uploadArquivo(file, pasta) {
  if (!file) return null
  const fileRef = ref(storage, `${pasta}/${Date.now()}_${file.name}`)
  await uploadBytes(fileRef, file)
  return getDownloadURL(fileRef)
}
