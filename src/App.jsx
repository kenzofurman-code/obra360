// src/App.jsx
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Obras from './pages/Obras'
import Locais from './pages/Locais'
import Visita from './pages/Visita'
import Upload from './pages/Upload'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        {/* "/" agora e' o seletor de projeto (Obras.jsx) - Home.jsx (lista
            plana de todas as vistorias, sem agrupar por obra) fica orfao por
            enquanto, nao removido do repo caso vire util como visao "todas
            as vistorias" mais pra frente. */}
        <Route path="/" element={<Obras />} />
        <Route path="/obra/:obraId" element={<Locais />} />
        <Route path="/obra/:obraId/local/:localId/upload" element={<Upload />} />
        {/* Rota legada (sem obra/local associado) - mantida por compatibilidade
            com o fluxo de "Link Manual" e vistorias antigas. */}
        <Route path="/upload" element={<Upload />} />
        <Route path="/visita/:id" element={<Visita />} />
      </Routes>
    </BrowserRouter>
  )
}
