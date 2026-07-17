import { HashRouter, Routes, Route } from 'react-router-dom'
import Dashboard from './pages/Dashboard'
import Recording from './pages/Recording'
import MeetingDetail from './pages/MeetingDetail'
import Settings from './pages/Settings'

export default function App() {
  return (
    <HashRouter>
      <Routes>
        <Route path="/" element={<Dashboard />} />
        <Route path="/recording/:id" element={<Recording />} />
        <Route path="/meeting/:id" element={<MeetingDetail />} />
        <Route path="/settings" element={<Settings />} />
      </Routes>
    </HashRouter>
  )
}
