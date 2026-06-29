// src/App.jsx
import { BrowserRouter, Routes, Route } from 'react-router-dom'
import Home from './pages/Home'
import Visita from './pages/Visita'
import Upload from './pages/Upload'

export default function App() {
  return (
    <BrowserRouter>
      <Routes>
        <Route path="/" element={<Home />} />
        <Route path="/upload" element={<Upload />} />
        <Route path="/visita/:id" element={<Visita />} />
      </Routes>
    </BrowserRouter>
  )
}
