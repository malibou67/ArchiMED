import { BrowserRouter, Routes, Route, Navigate } from 'react-router-dom';
import { ThemeProvider, createTheme, CssBaseline } from '@mui/material';
import Layout from './components/Layout';
import SearchPage from './pages/SearchPage';
import ModelsPage from './pages/ModelsPage';
import OcrPage from './pages/OcrPage';
import OcrTabsLayout from './pages/OcrTabsLayout';
import CollectionsPage from './pages/CollectionsPage';
import CollectionStatsPage from './pages/CollectionStatsPage';
import RegistreViewerPage from './pages/RegistreViewerPage';
import IndexesPage from './pages/IndexesPage';
import IndexVocabularyPage from './pages/IndexVocabularyPage';
import WordPagesPage from './pages/WordPagesPage';
import TasksPage from './pages/TasksPage';
import SettingsPage from './pages/SettingsPage';
import { TasksProvider } from './context/TasksContext';
import { LoadingProvider } from './context/LoadingContext';
import { HeaderProvider } from './context/HeaderContext';
import GlobalLoadingOverlay from './components/GlobalLoadingOverlay';

const theme = createTheme({
  palette: {
    mode: 'light',
    primary: {
      main: '#1e3a5f',
      light: '#26517c',
      dark: '#16293f',
    },
    secondary: {
      main: '#dc004e',
    },
  },
});

function App() {
  return (
    <ThemeProvider theme={theme}>
      <CssBaseline />
      <BrowserRouter>
        <LoadingProvider>
          <HeaderProvider>
          <TasksProvider>
            <GlobalLoadingOverlay />
            <Routes>
            <Route path="/" element={<Layout />}>
              <Route index element={<SearchPage />} />
              <Route path="ocr" element={<OcrTabsLayout />}>
                <Route index element={<OcrPage />} />
                <Route path="models" element={<ModelsPage />} />
              </Route>
              {/* Ancienne URL — redirection pour les favoris/liens existants */}
              <Route path="models" element={<Navigate to="/ocr/models" replace />} />
              <Route path="collections" element={<CollectionsPage />} />
              <Route path="collections/:collectionId/stats" element={<CollectionStatsPage />} />
              <Route path="collections/:collectionId/registres/:registreId" element={<RegistreViewerPage />} />
              <Route path="indexes" element={<IndexesPage />} />
              <Route path="indexes/:indexId" element={<IndexVocabularyPage />} />
              <Route path="indexes/:indexId/words/:word" element={<WordPagesPage />} />
              <Route path="tasks" element={<TasksPage />} />
              <Route path="settings" element={<SettingsPage />} />
            </Route>
            </Routes>
          </TasksProvider>
          </HeaderProvider>
        </LoadingProvider>
      </BrowserRouter>
    </ThemeProvider>
  );
}

export default App;
