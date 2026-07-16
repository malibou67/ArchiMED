import { useState, ReactElement } from 'react';
import { Outlet, useNavigate, useLocation, Link as RouterLink } from 'react-router-dom';
import {
  Box,
  Drawer,
  AppBar,
  Toolbar,
  List,
  Typography,
  Divider,
  IconButton,
  ListItem,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Badge,
  Tooltip,
  CircularProgress,
  Tabs,
  Tab,
} from '@mui/material';
import {
  Menu as MenuIcon,
  Search as SearchIcon,
  DocumentScanner as DocumentScannerIcon,
  FolderOpen as FolderOpenIcon,
  List as ListIcon,
  PendingActions as PendingActionsIcon,
  Settings as SettingsIcon,
  ModelTraining as ModelTrainingIcon,
} from '@mui/icons-material';
import { useTranslation } from 'react-i18next';
import { useTasks } from '../context/TasksContext';
import { useHeaderContext } from '../context/HeaderContext';
import TaskWidget from './TaskWidget';
import DataDirGuard from './DataDirGuard';

const drawerWidth = 240;

interface MenuItem {
  labelKey: string;
  icon: ReactElement;
  path: string;
}

const menuItems: MenuItem[] = [
  { labelKey: 'nav.search', icon: <SearchIcon />, path: '/' },
  { labelKey: 'nav.ocr', icon: <DocumentScannerIcon />, path: '/ocr' },
  { labelKey: 'nav.collections', icon: <FolderOpenIcon />, path: '/collections' },
  { labelKey: 'nav.indexes', icon: <ListIcon />, path: '/indexes' },
  { labelKey: 'nav.tasks', icon: <PendingActionsIcon />, path: '/tasks' },
];

// Épinglé tout en bas de la barre latérale.
const settingsItem: MenuItem = { labelKey: 'nav.settings', icon: <SettingsIcon />, path: '/settings' };

// Clé de titre affichée dans la bannière, par page (déplacé depuis l'en-tête des composants).
const pageTitleKeys: Record<string, string> = {
  '/': 'pageTitles.search',
  '/ocr': 'pageTitles.ocr',
  '/ocr/models': 'pageTitles.ocrModels',
  '/collections': 'pageTitles.collections',
  '/indexes': 'pageTitles.indexes',
  '/tasks': 'pageTitles.tasks',
  '/settings': 'pageTitles.settings',
};

export default function Layout() {
  const { t } = useTranslation('common');
  const [mobileOpen, setMobileOpen] = useState(false);
  const navigate = useNavigate();
  const location = useLocation();
  const { runningTasks, pausedTasks, queuedCount, openWidget } = useTasks();
  const { content: headerContent } = useHeaderContext();

  const runningCount = runningTasks.length;
  // On n'affiche le bouton des tâches que s'il y a une tâche en cours ou en pause.
  const showTasksButton = runningCount > 0 || pausedTasks.length > 0;
  const tasksTooltip = runningCount > 0
    ? t('tasksIndicator.running', { count: runningCount })
      + (queuedCount > 0 ? t('tasksIndicator.queuedSuffix', { count: queuedCount }) : '')
    : queuedCount > 0
      ? t('tasksIndicator.queued', { count: queuedCount })
      : t('tasksIndicator.none');

  // Titre de la bannière : clé mappée, sinon repli sur les routes dynamiques.
  const titleKey = pageTitleKeys[location.pathname];
  const pageTitle = titleKey
    ? t(titleKey)
    : location.pathname.includes('/registres/') ? t('pageTitles.registreViewer')
    : /^\/indexes\/[^/]+\/words\//.test(location.pathname) ? t('pageTitles.wordPages')
    : /^\/indexes\/[^/]+/.test(location.pathname) ? t('pageTitles.indexContent')
    : t('pageTitles.appDefault');

  const handleDrawerToggle = () => {
    setMobileOpen(!mobileOpen);
  };

  const handleNavigate = (path: string) => {
    navigate(path);
    setMobileOpen(false);
  };

  const renderItem = (item: MenuItem) => (
    <ListItem key={item.path} disablePadding>
      <ListItemButton
        selected={
          item.path === '/'
            ? location.pathname === '/'
            : location.pathname === item.path || location.pathname.startsWith(item.path + '/')
        }
        onClick={() => handleNavigate(item.path)}
        sx={{
          '&.Mui-selected': {
            color: 'primary.main',
            '& .MuiListItemIcon-root': { color: 'primary.main' },
            '& .MuiListItemText-primary': { fontWeight: 700 },
          },
        }}
      >
        <ListItemIcon>{item.icon}</ListItemIcon>
        <ListItemText primary={t(item.labelKey)} />
      </ListItemButton>
    </ListItem>
  );

  const drawer = (
    <Box sx={{ display: 'flex', flexDirection: 'column', height: '100%' }}>
      <Toolbar sx={{ justifyContent: 'center' }}>
        <Typography
          variant="h6"
          noWrap
          component="div"
          sx={{ fontWeight: 800, letterSpacing: '.04em', lineHeight: 1, color: 'text.primary' }}
        >
          Archi<Box component="span" sx={{ color: 'primary.main' }}>MED</Box>
        </Typography>
      </Toolbar>
      <Divider />
      <List>{menuItems.map(renderItem)}</List>
      <Box sx={{ flexGrow: 1 }} />
      <Divider />
      <List>{renderItem(settingsItem)}</List>
    </Box>
  );

  return (
    <Box sx={{ display: 'flex' }}>
      <AppBar
        position="fixed"
        elevation={0}
        sx={{
          width: { sm: `calc(100% - ${drawerWidth}px)` },
          ml: { sm: `${drawerWidth}px` },
          // Bleu profond sobre (moins criard que le bleu MUI vif, pas aussi sombre que le charbon)
          bgcolor: '#1e3a5f',
          borderBottom: '1px solid rgba(255,255,255,0.12)',
        }}
      >
        <Toolbar>
          <IconButton
            color="inherit"
            aria-label="open drawer"
            edge="start"
            onClick={handleDrawerToggle}
            sx={{ mr: 2, display: { sm: 'none' } }}
          >
            <MenuIcon />
          </IconButton>
          {headerContent ? (
            <Box sx={{ flexGrow: 1, minWidth: 0 }}>{headerContent}</Box>
          ) : location.pathname === '/ocr' || location.pathname.startsWith('/ocr/') ? (
            <Tabs
              value={location.pathname.startsWith('/ocr/models') ? '/ocr/models' : '/ocr'}
              textColor="inherit"
              sx={{
                flexGrow: 1,
                minHeight: 64,
                '& .MuiTab-root': { minHeight: 64, fontSize: '1rem' },
                '& .MuiTabs-indicator': { backgroundColor: 'common.white', height: 3 },
              }}
            >
              <Tab
                value="/ocr"
                label={t('ocrTabs.textRecognition')}
                icon={<DocumentScannerIcon fontSize="small" />}
                iconPosition="start"
                component={RouterLink}
                to="/ocr"
              />
              <Tab
                value="/ocr/models"
                label={t('ocrTabs.modelManagement')}
                icon={<ModelTrainingIcon fontSize="small" />}
                iconPosition="start"
                component={RouterLink}
                to="/ocr/models"
              />
            </Tabs>
          ) : (
            <Typography variant="h6" noWrap component="div" sx={{ flexGrow: 1 }}>
              {pageTitle}
            </Typography>
          )}

          {/* Indicateur global des tâches — affiché uniquement en cas d'activité */}
          {showTasksButton && (
            <Tooltip title={tasksTooltip} arrow>
              <IconButton color="inherit" onClick={openWidget} aria-label={t('nav.tasks')}>
                <Badge
                  badgeContent={queuedCount}
                  color="secondary"
                  overlap="circular"
                  invisible={queuedCount === 0}
                >
                  {runningCount > 0 ? (
                    <Box sx={{ position: 'relative', display: 'inline-flex' }}>
                      <CircularProgress size={24} thickness={5} color="inherit" />
                      <PendingActionsIcon sx={{ position: 'absolute', top: 3, left: 3, fontSize: 18 }} />
                    </Box>
                  ) : (
                    <PendingActionsIcon />
                  )}
                </Badge>
              </IconButton>
            </Tooltip>
          )}
        </Toolbar>
      </AppBar>
      <Box
        component="nav"
        sx={{ width: { sm: drawerWidth }, flexShrink: { sm: 0 } }}
      >
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={handleDrawerToggle}
          ModalProps={{
            keepMounted: true,
          }}
          sx={{
            display: { xs: 'block', sm: 'none' },
            '& .MuiDrawer-paper': { boxSizing: 'border-box', width: drawerWidth },
          }}
        >
          {drawer}
        </Drawer>
        <Drawer
          variant="permanent"
          sx={{
            display: { xs: 'none', sm: 'block' },
            '& .MuiDrawer-paper': { boxSizing: 'border-box', width: drawerWidth },
          }}
          open
        >
          {drawer}
        </Drawer>
      </Box>
      <Box
        component="main"
        sx={{
          flexGrow: 1,
          p: 3,
          width: { sm: `calc(100% - ${drawerWidth}px)` },
          minWidth: 0,
        }}
      >
        <Toolbar />
        <DataDirGuard>
          <Outlet />
        </DataDirGuard>
      </Box>

      {/* Widget flottant des tâches — visible sur toutes les pages */}
      <TaskWidget />
    </Box>
  );
}
