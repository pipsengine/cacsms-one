import React from 'react';
import {createRoot} from 'react-dom/client';
import {BrowserRouter, Navigate, Route, Routes} from 'react-router-dom';
import Hub from './Hub.jsx';
import SportsPortal from './sports/SportsPortal.jsx';
import './styles.css';
import './sports/sports.css';

createRoot(document.getElementById('root')).render(
  <React.StrictMode>
    <BrowserRouter>
      <Routes>
        <Route path="/sports/*" element={<SportsPortal />} />
        <Route path="/hub" element={<Hub />} />
        <Route path="/" element={<Hub />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </BrowserRouter>
  </React.StrictMode>
);
