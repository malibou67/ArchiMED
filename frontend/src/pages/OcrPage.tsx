import { useState, useEffect, useMemo, useRef } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Typography,
  Box,
  Backdrop,
  CircularProgress,
  Alert,
  Checkbox,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Collapse,
  Chip,
  Button,
  Radio,
  RadioGroup,
  FormControlLabel,
  Stack,
  Tooltip,
  TextField,
  InputAdornment,
  IconButton,
  Paper,
  ToggleButton,
  ToggleButtonGroup,
  Dialog,
  DialogTitle,
  DialogContent,
  DialogContentText,
  DialogActions,
} from '@mui/material';
import {
  ExpandMore as ExpandMoreIcon,
  ChevronRight as ChevronRightIcon,
  Folder as FolderIcon,
  Book as BookIcon,
  Image as ImageIcon,
  Cancel as CancelIcon,
  HourglassEmpty as HourglassEmptyIcon,
  Search as SearchIcon,
  Clear as ClearIcon,
  Circle as CircleIcon,
  CheckCircle as CheckCircleIcon,
  RadioButtonUnchecked as RadioButtonUncheckedIcon,
  ModelTraining as ModelTrainingIcon,
  FolderOpen as FolderOpenIcon,
  DocumentScanner as DocumentScannerIcon,
} from '@mui/icons-material';
import type { ReactNode } from 'react';
import { useTranslation } from 'react-i18next';
import { collectionsApi } from '../api/collections';
import { modelsApi } from '../api/models';
import { systemApi, SystemRequirements } from '../api/system';
import { ocrApi, OcrPageRef, OcrScopeItem, OcrScopeOnly } from '../api/ocr';
import { registresApi } from '../api/registres';
import { tasksApi, taskWorkMs, Task } from '../api/tasks';
import { useTasks } from '../context/TasksContext';
import EnvStatusChip from '../components/ocr/EnvStatusChip';
import OcrLaunchBar from '../components/ocr/OcrLaunchBar';
import { usePageLoading } from '../context/LoadingContext';
import { CollectionMetadata, ModelMetadata, RegistreSummary } from '../types';

const fmtNum = (n: number) => n.toLocaleString('fr-FR');

// Normalisation pour la recherche : minuscules, sans diacritiques.
const norm = (s: string) => s.normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase();

function PanelHeader({ title }: { title: string }) {
  return (
    <Box sx={{
      px: 2, py: 1.5,
      borderBottom: 1, borderColor: 'divider',
      flexShrink: 0,
    }}>
      <Typography
        variant="overline"
        sx={{ fontSize: '0.7rem', fontWeight: 700, letterSpacing: 1, color: 'text.secondary' }}
      >
        {title}
      </Typography>
    </Box>
  );
}

type Blocker = { title: string; detail: string; action?: { label: string; path: string } };

type Requirement = {
  key: string;
  ok: boolean;
  loading: boolean;
  icon: ReactNode;
  title: string;
  okDetail: string;
  todoDetail: string;
  action: { label: string; path: string };
};

export default function OcrPage() {
  const { t } = useTranslation(['ocr', 'common']);
  const navigate = useNavigate();
  const [collections, setCollections] = useState<CollectionMetadata[]>([]);
  const [models, setModels] = useState<ModelMetadata[]>([]);
  const [selectedSegModel, setSelectedSegModel] = useState('');
  const [selectedOcrModel, setSelectedOcrModel] = useState('');
  const [collectionsLoading, setCollectionsLoading] = useState(true);
  const [modelsLoading, setModelsLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [requirements, setRequirements] = useState<SystemRequirements | null>(null);
  const [reqLoading, setReqLoading] = useState(true);
  const [reqFailed, setReqFailed] = useState(false);

  // Loader d'étapes : reflète l'étape de chargement réellement en cours, avec compteur.
  // La vérif d'environnement (lente : import torch/kraken) n'est volontairement PAS dans
  // le loader — elle tourne en tâche de fond et s'affiche via EnvStatusChip / OcrLaunchBar,
  // pour que la page apparaisse aussi vite que les autres.
  const loadSteps = [
    { active: modelsLoading,      label: t('loading.models') },
    { active: collectionsLoading, label: t('loading.collections') },
  ];
  const stepsDone = loadSteps.filter((s) => !s.active).length;
  const currentStep = loadSteps.find((s) => s.active);
  usePageLoading(
    currentStep ? `${t('loading.ocr', { step: stepsDone + 1, total: loadSteps.length })}\n${currentStep.label}` : '',
    !!currentStep,
  );

  const [expandedCollections, setExpandedCollections] = useState<Set<string>>(new Set());
  const [expandedRegistres, setExpandedRegistres] = useState<Set<string>>(new Set());
  // Sélection à deux étages, exclusifs l'un de l'autre pour un registre donné :
  //  - `selectedRegistres` : registres cochés en entier, une seule entrée `collection/registre`.
  //    C'est ce qui rend instantané le cochage d'une collection de 1200 registres — on ne
  //    matérialise pas 780 000 clés, et le backend développera le périmètre depuis le disque.
  //  - `selectedPages` : pages cochées une à une, clés `collection/registre/page`.
  // Décocher une page d'un registre coché en entier le « matérialise » (cf. `togglePage`).
  const [selectedRegistres, setSelectedRegistres] = useState<Set<string>>(new Set());
  const [selectedPages, setSelectedPages] = useState<Set<string>>(new Set());
  const [treeFilter, setTreeFilter] = useState('');
  // Filtre à 3 états sur le statut de transcription (pour le modèle OCR choisi).
  const [statusFilter, setStatusFilter] = useState<'all' | 'missing' | 'done'>('all');

  // Stems déjà transcrits, par clé `${cKey}/${regFolder}/${modelId}` (chargés au dépliage).
  const [doneCache, setDoneCache] = useState<Map<string, Set<string>>>(new Map());
  const fetchingDone = useRef<Set<string>>(new Set());

  // Noms réels des pages, par clé `${cKey}/${regFolder}`, lus sur le disque au dépliage.
  // Seule source de noms : la pagination du metadata ne porte que des numéros et perd la
  // largeur du champ numérique, si bien que `pattern.replace('{num}', n)` fabriquait des
  // fichiers inexistants (`_1.jpg` pour un `_001.jpg` sur le disque).
  const [pagesCache, setPagesCache] = useState<Map<string, string[]>>(new Map());
  const fetchingPages = useRef<Set<string>>(new Set());

  // Historique des tâches OCR terminées (pour l'estimation de durée).
  const [pastOcrTasks, setPastOcrTasks] = useState<Task[]>([]);

  const { hasActivity, refresh: refreshTasks, runningTasks, pausedTasks, queuedTasks, pause, resume } = useTasks();
  // Tâche OCR contrôlable depuis cette page : celle de ce poste, en cours ou en pause.
  const activeOcr = [...runningTasks, ...pausedTasks].find((t) => t.type === 'ocr' && t.owned !== false) ?? null;

  // Registres déjà verrouillés pour le modèle OCR choisi, **tous postes confondus** — y compris
  // celui-ci, car le backend refuse aussi une seconde tâche locale sur le même périmètre.
  // Clé « collection/registre » = la `rKey` de l'arbre ; valeur = poste, vide si c'est le nôtre.
  // Doit rester le miroir de `TaskService._scope_keys`, repli cartésien compris.
  const busyRegistres = useMemo(() => {
    const out = new Map<string, string>();
    if (!selectedOcrModel) return out;
    for (const task of [...runningTasks, ...pausedTasks, ...queuedTasks]) {
      if (task.type !== 'ocr' || task.ocr_model !== selectedOcrModel) continue;
      const from = task.owned === false ? (task.machine_label ?? '?') : '';
      const pairs: [string, string][] = task.scopes
        ?? (task.collections ?? []).flatMap((c) =>
             (task.registres ?? []).map((r) => [c, r] as [string, string]));
      for (const [c, r] of pairs) out.set(`${c}/${r}`, from);
    }
    return out;
  }, [runningTasks, pausedTasks, queuedTasks, selectedOcrModel]);

  const busyLabel = (from: string) =>
    from ? t('tree.busyOn', { machine: from }) : t('tree.busyHere');

  // Création de la tâche : 'checking' = sondage des transcriptions des pages cochées à
  // l'unité (un appel par registre concerné), 'creating' = POST /api/ocr/run, où le backend
  // développe les périmètres depuis le disque. La seconde dure, d'où l'overlay bloquant.
  const [launchPhase, setLaunchPhase] = useState<null | 'checking' | 'creating'>(null);
  const launching = launchPhase !== null;
  // Avancement du sondage, registre par registre (seul repère pendant la phase 'checking').
  const [launchProgress, setLaunchProgress] = useState({ done: 0, total: 0 });
  // Pages effectivement envoyées, pour le détail de l'overlay pendant la phase 'creating'.
  const [launchPageCount, setLaunchPageCount] = useState(0);
  const [pausingOcr, setPausingOcr] = useState(false);
  const [queuedNotice, setQueuedNotice] = useState<number | null>(null);
  // Pages que le backend a écartées faute d'image sur le disque (registre modifié depuis
  // la dernière synchronisation) : à signaler, sinon le compte annoncé serait inexpliqué.
  const [skippedNotice, setSkippedNotice] = useState(0);
  // Confirmation d'écrasement. `pages`/`scopes` = la sélection telle quelle (l'ordre est celui
  // du traitement), `doneCount` = combien de pages y sont déjà transcrites avec le modèle
  // choisi, `missingPages` = les pages nommées qui restent (les périmètres, eux, se filtrent
  // côté backend). null = pas de dialogue.
  const [overwrite, setOverwrite] = useState<{
    pages: OcrPageRef[];
    scopes: OcrScopeItem[];
    total: number;
    doneCount: number;
    missingPages: OcrPageRef[];
    missingTotal: number;
  } | null>(null);

  // La pause est coopérative : elle ne prend effet qu'à la fin de la page en cours.
  const pauseOcr = async () => {
    if (!activeOcr) return;
    setPausingOcr(true);
    try { await pause(activeOcr.id); } finally { await refreshTasks(); }
  };

  const resumeOcr = async () => {
    if (!activeOcr) return;
    setPausingOcr(false);
    await resume(activeOcr.id);
    await refreshTasks();
  };

  useEffect(() => {
    loadCollections();
    loadModels();
    loadPastTasks();
    // Le contexte ne poll que s'il sait déjà qu'une tâche tourne : sans ce relevé, une appli
    // restée ouverte au repos ignorerait un OCR démarré entre-temps sur un autre poste.
    refreshTasks();
    systemApi.getRequirements()
      .then((r) => { setRequirements(r); setReqFailed(false); })
      .catch(() => { setRequirements(null); setReqFailed(true); })
      .finally(() => setReqLoading(false));
  }, []);

  const loadCollections = async () => {
    try {
      setCollectionsLoading(true);
      setCollections(await collectionsApi.getAll());
    } catch (err) {
      setError(t('errors.loadCollections'));
      console.error(err);
    } finally {
      setCollectionsLoading(false);
    }
  };

  const loadModels = async () => {
    try {
      setModelsLoading(true);
      const data = await modelsApi.getAll();
      setModels(data);
      const seg = data.find((m) => m.type === 'segmentation');
      const ocr = data.find((m) => m.type === 'ocr');
      if (seg) setSelectedSegModel(seg.id);
      if (ocr) setSelectedOcrModel(ocr.id);
    } catch (err) {
      setError(t('errors.loadModels'));
      console.error(err);
    } finally {
      setModelsLoading(false);
    }
  };

  const loadPastTasks = () => {
    tasksApi.list()
      .then((tasks) => setPastOcrTasks(tasks.filter((t) =>
        t.type === 'ocr' && t.status === 'done' && t.processed > 0 && !!t.started_at,
      )))
      .catch(() => {});
  };

  // Quand l'activité OCR globale s'arrête : rafraîchir compteurs, états par page et historique.
  const prevActivity = useRef(false);
  useEffect(() => {
    if (prevActivity.current && !hasActivity) {
      loadCollections();
      setDoneCache(new Map());
      setPagesCache(new Map());
      loadPastTasks();
    }
    prevActivity.current = hasActivity;
  }, [hasActivity]);

  // « Mise en pause… » ne concerne qu'une tâche encore en cours (sinon l'état resterait collé
  // et polluerait la prochaine transcription).
  useEffect(() => {
    if (activeOcr?.status !== 'running') setPausingOcr(false);
  }, [activeOcr?.status]);

  // Le runner publie chaque registre dès qu'il est terminé (metadata déjà à jour côté backend) :
  // on recharge les compteurs sans attendre la fin de la tâche.
  const doneRegistresCount = activeOcr?.registres_done?.length ?? 0;
  const prevDoneRegistres = useRef(0);
  useEffect(() => {
    if (doneRegistresCount > prevDoneRegistres.current) {
      loadCollections();
      setDoneCache(new Map());
    }
    prevDoneRegistres.current = doneRegistresCount;
  }, [doneRegistresCount]);

  const colKey = (col: CollectionMetadata) => col.folder_name || col.type;

  // Charge les noms de pages des registres dépliés. Un aller-retour par registre, mis en
  // cache : l'arbre replié, lui, n'en a pas besoin (ses compteurs viennent de `pages_count`).
  useEffect(() => {
    for (const collection of collections) {
      const cKey = colKey(collection);
      for (const reg of collection.registres || []) {
        const rKey = `${cKey}/${reg.folder_name}`;
        if (!expandedRegistres.has(rKey)) continue;
        if (pagesCache.has(rKey) || fetchingPages.current.has(rKey)) continue;
        fetchingPages.current.add(rKey);
        registresApi.getPages(cKey, reg.folder_name)
          .then((names) => setPagesCache((prev) => new Map(prev).set(rKey, names)))
          .catch(() => {})
          .finally(() => fetchingPages.current.delete(rKey));
      }
    }
  }, [collections, expandedRegistres, pagesCache]);

  // Charge les stems transcrits des registres dépliés (pour le modèle OCR choisi).
  useEffect(() => {
    if (!selectedOcrModel) return;
    for (const collection of collections) {
      const cKey = colKey(collection);
      for (const reg of collection.registres || []) {
        const rKey = `${cKey}/${reg.folder_name}`;
        if (!expandedRegistres.has(rKey)) continue;
        const cacheKey = `${rKey}/${selectedOcrModel}`;
        if (doneCache.has(cacheKey) || fetchingDone.current.has(cacheKey)) continue;
        fetchingDone.current.add(cacheKey);
        ocrApi.donePages(cKey, reg.folder_name, selectedOcrModel)
          .then((stems) => setDoneCache((prev) => new Map(prev).set(cacheKey, new Set(stems))))
          .catch(() => {})
          .finally(() => fetchingDone.current.delete(cacheKey));
      }
    }
  }, [collections, expandedRegistres, selectedOcrModel, doneCache]);

  const parsePageKey = (key: string): OcrPageRef | null => {
    const first = key.indexOf('/');
    const second = key.indexOf('/', first + 1);
    if (first < 0 || second < 0) return null;
    return {
      collection: key.substring(0, first),
      registre: key.substring(first + 1, second),
      page: key.substring(second + 1),
    };
  };

  // Envoi effectif. L'éventuelle confirmation d'écrasement a déjà été tranchée par `requestLaunch`.
  const doLaunch = async (pages: OcrPageRef[], scopes: OcrScopeItem[], scopeOnly: OcrScopeOnly) => {
    if ((pages.length === 0 && scopes.length === 0) || !selectedSegModel || !selectedOcrModel) return;
    try {
      // Un périmètre n'annonce pas son propre volume : on affiche le total déjà calculé.
      setLaunchPageCount(scopes.length > 0 ? selectedTotal : pages.length);
      setLaunchPhase('creating');
      setError(null);
      // `total` et non le compte envoyé : le backend développe les périmètres depuis le
      // disque, et écarte les pages nommées dont l'image aurait disparu entre-temps.
      const task = await ocrApi.run(selectedSegModel, selectedOcrModel, pages, scopes, scopeOnly);
      await refreshTasks();
      setQueuedNotice(task.total);
      setSkippedNotice(task.skipped_missing ?? 0);
      clearSelection(); // on garde les modèles, on libère la sélection pour enchaîner
    } catch (err: any) {
      setError(err?.response?.data?.detail || t('errors.launch'));
      console.error(err);
    } finally {
      setLaunchPhase(null);
    }
  };

  const pageKey = (colId: string, regFolder: string, page: string) => `${colId}/${regFolder}/${page}`;

  // ── Statut de transcription par page / filtre ─────────────────────
  // Stem = nom de page sans extension (clé partagée par les XML produits, cf. backend).
  const pageStem = (page: string) => page.replace(/\.[^.]+$/, '');

  // Set des stems transcrits pour (collection, registre, modèle OCR courant). Réutilise le
  // cache des pastilles ; charge à la demande (sélection sur registre non déplié).
  const ensureDoneSet = async (cKey: string, regFolder: string): Promise<Set<string>> => {
    const cacheKey = `${cKey}/${regFolder}/${selectedOcrModel}`;
    const cached = doneCache.get(cacheKey);
    if (cached) return cached;
    const stems = await ocrApi.donePages(cKey, regFolder, selectedOcrModel);
    const set = new Set(stems);
    setDoneCache((prev) => new Map(prev).set(cacheKey, set));
    return set;
  };

  const regPagesDone = (reg: RegistreSummary) =>
    selectedOcrModel ? (reg.ocr_status?.[selectedOcrModel]?.pages_done ?? 0) : 0;

  // Vrai si le registre contient au moins une page correspondant au filtre courant.
  const regMatchesFilter = (reg: RegistreSummary): boolean => {
    if (statusFilter === 'all') return true;
    const done = regPagesDone(reg);
    if (statusFilter === 'missing') return reg.pages_count > 0 && done < reg.pages_count;
    return done > 0; // 'done'
  };

  // Nombre de pages correspondant au filtre (dénominateur des cases à cocher).
  // `pages_count` est le compte relevé sur le disque à la dernière synchronisation : un entier
  // déjà en mémoire, donc utilisable pendant le rendu, là où les **noms** des pages demandent
  // un aller-retour.
  const matchingTotal = (reg: RegistreSummary): number => {
    const total = reg.pages_count;
    if (statusFilter === 'all') return total;
    const done = regPagesDone(reg);
    return statusFilter === 'missing' ? Math.max(0, total - done) : done;
  };

  // Registre retrouvé depuis une clé « collection/registre » : les périmètres sélectionnés ne
  // portent que des clés, mais les compteurs ont besoin du registre.
  const regByKey = useMemo(() => {
    const out = new Map<string, RegistreSummary>();
    for (const collection of collections) {
      const cKey = colKey(collection);
      for (const reg of collection.registres || []) out.set(`${cKey}/${reg.folder_name}`, reg);
    }
    return out;
  }, [collections]);

  // Pages cochées à l'unité, comptées par préfixe « collection/ » et « collection/registre/ ».
  const selectionCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const key of selectedPages) {
      const firstSlash = key.indexOf('/');
      const secondSlash = key.indexOf('/', firstSlash + 1);
      if (firstSlash >= 0) {
        const cp = key.substring(0, firstSlash + 1);
        counts.set(cp, (counts.get(cp) || 0) + 1);
      }
      if (secondSlash >= 0) {
        const rp = key.substring(0, secondSlash + 1);
        counts.set(rp, (counts.get(rp) || 0) + 1);
      }
    }
    return counts;
  }, [selectedPages]);

  // Total annoncé dans la barre de lancement : pages cochées à l'unité + périmètres.
  const selectedTotal = useMemo(() => {
    let total = selectedPages.size;
    for (const rKey of selectedRegistres) {
      const reg = regByKey.get(rKey);
      if (reg) total += matchingTotal(reg);
    }
    return total;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selectedPages, selectedRegistres, regByKey, statusFilter, selectedOcrModel]);

  // Registres distincts couverts : les périmètres, plus ceux qui n'ont que des pages à l'unité.
  const selectedRegistreCount = useMemo(
    () => selectedRegistres.size
      + [...selectionCounts.keys()].filter((k) => k.indexOf('/') !== k.lastIndexOf('/')).length,
    [selectionCounts, selectedRegistres],
  );

  const getRegCheckState = (colId: string, reg: RegistreSummary) => {
    const rKey = `${colId}/${reg.folder_name}`;
    if (selectedRegistres.has(rKey)) return { checked: true, indeterminate: false };
    return { checked: false, indeterminate: (selectionCounts.get(`${rKey}/`) || 0) > 0 };
  };

  const getColCheckState = (collection: CollectionMetadata) => {
    const cKey = colKey(collection);
    const regs = (collection.registres || []).filter((r) => matchingTotal(r) > 0);
    if (regs.length === 0) return { checked: false, indeterminate: false };
    let whole = 0;
    let partial = 0;
    for (const r of regs) {
      if (selectedRegistres.has(`${cKey}/${r.folder_name}`)) whole += 1;
      else if ((selectionCounts.get(`${cKey}/${r.folder_name}/`) || 0) > 0) partial += 1;
    }
    return {
      checked: whole === regs.length,
      indeterminate: whole + partial > 0 && whole < regs.length,
    };
  };

  const toggleCollection = (cKey: string) => {
    setExpandedCollections((prev) => {
      const next = new Set(prev);
      next.has(cKey) ? next.delete(cKey) : next.add(cKey);
      return next;
    });
  };

  const toggleRegistre = (rKey: string) => {
    setExpandedRegistres((prev) => {
      const next = new Set(prev);
      next.has(rKey) ? next.delete(rKey) : next.add(rKey);
      return next;
    });
  };

  // Retire un registre de la sélection, sous quelque forme qu'il y figure.
  const dropRegistres = (rKeys: string[]) => {
    if (rKeys.length === 0) return;
    const drop = new Set(rKeys);
    setSelectedRegistres((prev) => {
      const next = new Set([...prev].filter((k) => !drop.has(k)));
      return next.size === prev.size ? prev : next;
    });
    setSelectedPages((prev) => {
      const next = new Set([...prev].filter((k) => {
        const second = k.indexOf('/', k.indexOf('/') + 1);
        return second < 0 || !drop.has(k.substring(0, second));
      }));
      return next.size === prev.size ? prev : next;
    });
  };

  // Coche/décoche **une** page. Si son registre était coché en entier, il faut le matérialiser :
  // la sélection passe du périmètre à ses pages visibles, moins celle que l'on décoche.
  const togglePage = (rKey: string, page: string, visible: string[]) => {
    const key = `${rKey}/${page}`;
    if (selectedRegistres.has(rKey)) {
      setSelectedRegistres((prev) => {
        const next = new Set(prev);
        next.delete(rKey);
        return next;
      });
      setSelectedPages((prev) => {
        const next = new Set(prev);
        for (const p of visible) if (p !== page) next.add(`${rKey}/${p}`);
        return next;
      });
      return;
    }
    setSelectedPages((prev) => {
      const next = new Set(prev);
      next.has(key) ? next.delete(key) : next.add(key);
      return next;
    });
  };

  // Case d'un registre : tout ou rien, sans aucun aller-retour réseau — c'est le périmètre qui
  // est retenu, le backend le développera depuis le disque au lancement.
  const toggleAllPages = (colId: string, reg: RegistreSummary) => {
    const rKey = `${colId}/${reg.folder_name}`;
    if (busyRegistres.has(rKey)) return;
    const state = getRegCheckState(colId, reg);
    if (state.checked || state.indeterminate) {
      dropRegistres([rKey]);
      return;
    }
    if (matchingTotal(reg) === 0) return;
    setSelectedRegistres((prev) => new Set(prev).add(rKey));
  };

  // Cocher une collection de 1200 registres reste instantané : on n'ajoute que 1200 clés de
  // périmètre, jamais les centaines de milliers de noms de pages qu'elles recouvrent.
  const toggleAllCollection = (collection: CollectionMetadata) => {
    const cKey = colKey(collection);
    // Les registres verrouillés ailleurs sont exclus du lot : les cocher ne mènerait qu'à un refus.
    const keys = (collection.registres || [])
      .filter((r) => !busyRegistres.has(`${cKey}/${r.folder_name}`) && matchingTotal(r) > 0)
      .map((r) => `${cKey}/${r.folder_name}`);
    if (keys.length > 0 && keys.every((k) => selectedRegistres.has(k))) {
      dropRegistres(keys);
      return;
    }
    dropRegistres(keys);   // les pages cochées à l'unité seraient comptées deux fois
    setSelectedRegistres((prev) => {
      const next = new Set(prev);
      keys.forEach((k) => next.add(k));
      return next;
    });
  };

  const clearSelection = () => {
    setSelectedPages(new Set());
    setSelectedRegistres(new Set());
  };

  // ── Périmètres verrouillés touchés par la sélection ────────────────
  // Une sélection faite avant qu'un autre poste ne démarre — ou conservée après un changement
  // de modèle OCR — peut viser un registre devenu occupé : filet avant l'envoi.
  const blockedSelection = useMemo(() => {
    const out = new Map<string, string>();
    const consider = (rKey: string) => {
      const from = busyRegistres.get(rKey);
      if (from !== undefined) out.set(rKey, from);
    };
    for (const rKey of selectedRegistres) consider(rKey);
    for (const key of selectedPages) {
      const second = key.indexOf('/', key.indexOf('/') + 1);
      if (second >= 0) consider(key.substring(0, second));
    }
    return out;
  }, [selectedPages, selectedRegistres, busyRegistres]);

  const dropBlockedSelection = () => dropRegistres([...blockedSelection.keys()]);

  // ── Lancement ──────────────────────────────────────────────────────
  // La sélection se traduit en deux listes : les pages nommées une à une, et les périmètres
  // (registres cochés en entier) que le backend développera depuis le disque.
  const selectionPayload = () => ({
    pages: [...selectedPages].map(parsePageKey).filter((p): p is OcrPageRef => p !== null),
    scopes: [...selectedRegistres].map((rKey) => {
      const slash = rKey.indexOf('/');
      return { collection: rKey.substring(0, slash), registre: rKey.substring(slash + 1) };
    }) as OcrScopeItem[],
  });

  // Confirmation si des transcriptions vont être écrasées, **sans toucher au disque** : les
  // périmètres se comptent sur l'`ocr_status` publié, les pages nommées à l'unité sur le cache
  // des pastilles. Le vrai relevé, lui, a lieu au lancement, côté backend.
  const requestLaunch = async () => {
    const { pages, scopes } = selectionPayload();
    setError(null);
    setLaunchProgress({ done: 0, total: 0 });
    setLaunchPhase('checking');
    try {
      let doneCount = 0;
      // Pages déjà transcrites d'un registre coché en entier : on lit `ocr_status`, le compte
      // publié dans le metadata — celui-là même qu'affiche la pastille `fait/total` de l'arbre.
      // Le relever sur le disque coûtait ~3,9 s par registre sur le partage réseau, soit plus
      // d'une heure pour une collection de 1200 registres, avant même d'avoir posé la question.
      // Sous le filtre « manquantes », le périmètre ne rapporte par construction que des pages
      // non transcrites : rien à écraser.
      if (statusFilter !== 'missing') {
        for (const rKey of selectedRegistres) {
          const reg = regByKey.get(rKey);
          if (reg) doneCount += Math.min(regPagesDone(reg), matchingTotal(reg));
        }
      }
      // Pages nommées à l'unité : un sondage par registre concerné, et seulement s'il annonce
      // au moins une page faite.
      const donePages: OcrPageRef[] = [];
      const byReg = new Map<string, OcrPageRef[]>();
      for (const ref of pages) {
        const rKey = `${ref.collection}/${ref.registre}`;
        const bucket = byReg.get(rKey);
        if (bucket) bucket.push(ref);
        else byReg.set(rKey, [ref]);
      }
      let checked = 0;
      if (byReg.size > 0) setLaunchProgress({ done: 0, total: byReg.size });
      for (const [rKey, refs] of byReg) {
        setLaunchProgress({ done: ++checked, total: byReg.size });
        const reg = regByKey.get(rKey);
        if (!reg || regPagesDone(reg) === 0) continue;
        const doneSet = await ensureDoneSet(refs[0].collection, refs[0].registre);
        for (const ref of refs) if (doneSet.has(pageStem(ref.page))) donePages.push(ref);
      }
      doneCount += donePages.length;

      if (doneCount === 0) {
        await doLaunch(pages, scopes, statusFilter);
        return;
      }
      const alreadyDone = new Set(donePages.map((r) => `${r.collection}/${r.registre}/${r.page}`));
      setOverwrite({
        pages,
        scopes,
        total: selectedTotal,
        doneCount,
        // « Ne transcrire que les manquantes » : les pages nommées sont filtrées ici, les
        // périmètres le seront côté backend par `scope_only`.
        missingPages: pages.filter(
          (r) => !alreadyDone.has(`${r.collection}/${r.registre}/${r.page}`)),
        missingTotal: selectedTotal - doneCount,
      });
    } catch (err: any) {
      setError(err?.response?.data?.detail || t('errors.launch'));
      console.error(err);
    } finally {
      setLaunchPhase(null);
    }
  };

  // ── Filtre de l'arbre (la sélection et les checkboxes restent sur les données complètes) ──
  const filterActive = treeFilter.trim().length > 0;
  const visibleTree = useMemo(() => {
    // 1) Filtre texte sur collections / registres.
    let tree: { collection: CollectionMetadata; regs: RegistreSummary[] }[];
    if (!filterActive) {
      tree = collections.map((c) => ({ collection: c, regs: c.registres || [] }));
    } else {
      const q = norm(treeFilter.trim());
      tree = [];
      for (const c of collections) {
        const regs = c.registres || [];
        if (norm(`${c.titre} ${c.folder_name ?? ''}`).includes(q)) {
          tree.push({ collection: c, regs });
          continue;
        }
        const matched = regs.filter((r) => norm(`${r.titre} ${r.folder_name}`).includes(q));
        if (matched.length > 0) tree.push({ collection: c, regs: matched });
      }
    }
    // 2) Filtre de statut : on retire les registres sans page correspondante, puis les
    //    collections devenues vides. Sous 'all' (ou sans modèle) : aucun masquage.
    if (statusFilter === 'all' || !selectedOcrModel) return tree;
    const out: { collection: CollectionMetadata; regs: RegistreSummary[] }[] = [];
    for (const { collection, regs } of tree) {
      const kept = regs.filter(regMatchesFilter);
      if (kept.length > 0) out.push({ collection, regs: kept });
    }
    return out;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [collections, treeFilter, filterActive, statusFilter, selectedOcrModel]);

  // ── Éléments bloquants : environnement (vérif asynchrone) et configuration ──
  const envBlockers: Blocker[] = [];
  if (!reqLoading) {
    if (reqFailed || !requirements) {
      envBlockers.push({
        title: t('env.checkFailedTitle'),
        detail: t('env.checkFailedDetail'),
      });
    } else {
      if (!(requirements.torch?.ok ?? false)) {
        envBlockers.push({
          title: t('env.torchTitle'),
          detail: t('env.torchDetail'),
        });
      }
      if (!(requirements.torchvision?.ok ?? false)) {
        envBlockers.push({
          title: t('env.torchvisionTitle'),
          detail: requirements.torchvision?.error || t('env.torchvisionDetail'),
        });
      }
      if (!(requirements.kraken?.ok ?? false)) {
        envBlockers.push({
          title: t('env.krakenTitle'),
          detail: requirements.kraken?.error || t('env.krakenDetail'),
        });
      }
    }
  }

  const segModelCount = models.filter((m) => m.type === 'segmentation').length;
  const ocrModelCount = models.filter((m) => m.type === 'ocr').length;

  // Prérequis de configuration affichés en checklist : chacun porte son état (ok)
  // une fois le chargement terminé. Les prérequis non satisfaits servent de blockers.
  const setupRequirements: Requirement[] = [
    {
      key: 'seg',
      ok: segModelCount > 0,
      loading: modelsLoading,
      icon: <ModelTrainingIcon />,
      title: t('setup.segTitle'),
      okDetail: t('setup.segAvailable', { count: segModelCount }),
      todoDetail: t('setup.segTodo'),
      action: { label: t('setup.addModels'), path: '/ocr/models' },
    },
    {
      key: 'ocr',
      ok: ocrModelCount > 0,
      loading: modelsLoading,
      icon: <DocumentScannerIcon />,
      title: t('setup.ocrTitle'),
      okDetail: t('setup.ocrAvailable', { count: ocrModelCount }),
      todoDetail: t('setup.ocrTodo'),
      action: { label: t('setup.addModels'), path: '/ocr/models' },
    },
    {
      key: 'col',
      ok: collections.length > 0,
      loading: collectionsLoading,
      icon: <FolderOpenIcon />,
      title: t('setup.colTitle'),
      okDetail: t('setup.colAvailable', { count: collections.length }),
      todoDetail: t('setup.colTodo'),
      action: { label: t('setup.manageCollections'), path: '/collections' },
    },
  ];

  // Blockers de configuration = prérequis chargés mais non satisfaits.
  const setupBlockers: Blocker[] = setupRequirements
    .filter((r) => !r.loading && !r.ok)
    .map((r) => ({ title: r.title, detail: r.todoDetail, action: r.action }));

  const blockers = [...envBlockers, ...setupBlockers];

  // Rendu d'une alerte de blocker (partagé entre la bannière d'environnement et
  // l'état vide de configuration qui remplace les deux colonnes).
  const renderBlockerAlert = (b: Blocker, i: number) => (
    <Alert
      key={i}
      severity="error"
      icon={<CancelIcon fontSize="small" />}
      variant="outlined"
      sx={{ py: 0 }}
      action={b.action ? (
        <Button
          color="error"
          size="small"
          variant="outlined"
          onClick={() => navigate(b.action!.path)}
          sx={{ alignSelf: 'center', whiteSpace: 'nowrap' }}
        >
          {b.action.label}
        </Button>
      ) : undefined}
    >
      <Typography variant="body2" fontWeight={600}>{b.title}</Typography>
      <Typography variant="caption" color="text.secondary">{b.detail}</Typography>
    </Alert>
  );

  const canLaunch = !launching && !reqLoading && blockers.length === 0
    && !!selectedSegModel && !!selectedOcrModel && selectedTotal > 0
    && blockedSelection.size === 0;
  let disabledReason: string | null = null;
  if (reqLoading) disabledReason = t('disabled.checking');
  else if (envBlockers.length > 0) disabledReason = t('disabled.envIncomplete');
  else if (setupBlockers.length > 0) disabledReason = t('disabled.configIncomplete');
  else if (!selectedSegModel || !selectedOcrModel) disabledReason = t('disabled.chooseModels');
  else if (blockedSelection.size > 0) disabledReason = t('disabled.selectionBusy', { count: blockedSelection.size });
  else if (selectedTotal === 0) disabledReason = t('disabled.selectPage');

  // ── Estimation de durée (débit des dernières tâches OCR terminées) ──
  const estimateSeconds = useMemo(() => {
    if (selectedTotal === 0 || pastOcrTasks.length === 0) return null;
    const device = requirements?.cuda?.ok ? 'cuda' : 'cpu';
    let sample = pastOcrTasks.filter((t) => t.preflight?.device === device);
    if (sample.length === 0) sample = pastOcrTasks;
    sample = sample.slice(0, 10); // les plus récentes (la liste est triée récentes d'abord)
    let pages = 0;
    let secs = 0;
    const maintenant = Date.now();
    for (const t of sample) {
      // Temps de travail cumulé, et non `finished_at - started_at` : une tâche passée qui avait
      // été mise en pause aurait sinon gonflé le débit et sous-estimé toutes les estimations.
      const ms = taskWorkMs(t, maintenant);
      const dur = ms == null ? 0 : ms / 1000;
      if (dur > 0) { pages += t.processed; secs += dur; }
    }
    if (pages === 0 || secs === 0) return null;
    return selectedTotal / (pages / secs);
  }, [selectedTotal, pastOcrTasks, requirements]);

  const modelRadioGroup = (type: 'segmentation' | 'ocr', value: string, onChange: (v: string) => void) => {
    const filtered = models.filter((m) => m.type === type);
    if (modelsLoading) {
      return (
        <Box sx={{ display: 'flex', alignItems: 'center', gap: 1.5, p: 2 }}>
          <CircularProgress size={16} />
          <Typography variant="body2" color="text.secondary">{t('common:loading.default')}</Typography>
        </Box>
      );
    }
    if (filtered.length === 0) {
      return (
        <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
          {t('models.none')}
        </Typography>
      );
    }
    return (
      <RadioGroup value={value} onChange={(e) => onChange(e.target.value)}>
        {filtered.map((model) => (
          <FormControlLabel
            key={model.id}
            value={model.id}
            sx={{ mx: 0, px: 1.5, py: 0.5, borderRadius: 1, '&:hover': { bgcolor: 'action.hover' } }}
            control={<Radio size="small" />}
            label={
              <Box>
                <Typography variant="body2" sx={{ fontWeight: 500 }}>{model.name}</Typography>
                <Typography variant="caption" color="text.secondary">
                  {[
                    model.version && `v${model.version}`,
                    model.accuracy && `${(model.accuracy * 100).toFixed(1)}%`,
                  ].filter(Boolean).join(' — ') || model.description || model.id}
                </Typography>
              </Box>
            }
          />
        ))}
      </RadioGroup>
    );
  };

  return (
    // Remplit la hauteur allouée par OcrTabsLayout (qui gère le calc vs la fenêtre)
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

      {/* ─── Alertes ─── */}
      {error && (
        <Alert severity="error" sx={{ mb: 1, flexShrink: 0 }} onClose={() => setError(null)}>
          {error}
        </Alert>
      )}
      {/* ─── Environnement OCR incomplet : bannière, la page reste utilisable ─── */}
      {envBlockers.length > 0 && (
        <Stack spacing={1} sx={{ mb: 1, flexShrink: 0 }}>
          {envBlockers.map((b, i) => renderBlockerAlert(b, i))}
        </Stack>
      )}

      {/* ─── Contenu principal ───
          Si la configuration empêche toute sélection (aucune collection ou aucun
          modèle), on remplace les deux colonnes vides par une checklist centrée. */}
      {setupBlockers.length > 0 ? (
        <Box sx={{ flex: 1, display: 'flex', alignItems: 'center', justifyContent: 'center', overflow: 'auto', p: 3 }}>
          <Stack spacing={3} alignItems="center" sx={{ width: '100%', maxWidth: 600 }}>
            <Stack spacing={1} alignItems="center" sx={{ textAlign: 'center' }}>
              <Box
                sx={{
                  width: 72, height: 72, borderRadius: '50%',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  bgcolor: 'action.selected', color: 'primary.main',
                  '& > svg': { fontSize: 38 },
                }}
              >
                <DocumentScannerIcon />
              </Box>
              <Typography variant="h6" sx={{ fontWeight: 700 }}>
                {t('ready.title')}
              </Typography>
              <Typography variant="body2" color="text.secondary" sx={{ maxWidth: 440 }}>
                {t('ready.subtitle')}
              </Typography>
            </Stack>

            <Paper variant="outlined" sx={{ width: '100%', borderRadius: 2, overflow: 'hidden' }}>
              {setupRequirements.map((r, i) => (
                <Box
                  key={r.key}
                  sx={{
                    display: 'flex', alignItems: 'center', gap: 2, px: 2.5, py: 2,
                    borderTop: i > 0 ? 1 : 0, borderColor: 'divider',
                    bgcolor: r.ok ? 'transparent' : 'action.hover',
                  }}
                >
                  <Box sx={{ display: 'flex', color: r.ok ? 'success.main' : 'text.disabled', '& > svg': { fontSize: 28 } }}>
                    {r.ok ? <CheckCircleIcon /> : <RadioButtonUncheckedIcon />}
                  </Box>
                  <Box sx={{ display: 'flex', color: 'text.secondary', '& > svg': { fontSize: 22 } }}>
                    {r.icon}
                  </Box>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Typography variant="subtitle2" sx={{ fontWeight: 600 }}>{r.title}</Typography>
                    <Typography variant="caption" color="text.secondary">
                      {r.ok ? r.okDetail : r.todoDetail}
                    </Typography>
                  </Box>
                  {r.ok ? (
                    <Chip size="small" label="OK" color="success" variant="outlined" sx={{ fontWeight: 600 }} />
                  ) : (
                    <Button
                      size="small"
                      variant="contained"
                      disableElevation
                      onClick={() => navigate(r.action.path)}
                      sx={{ whiteSpace: 'nowrap', flexShrink: 0 }}
                    >
                      {r.action.label}
                    </Button>
                  )}
                </Box>
              ))}
            </Paper>
          </Stack>
        </Box>
      ) : (
      <Box sx={{ flex: 1, display: 'flex', overflow: 'hidden' }}>

        {/* Colonne gauche : sélection des pages */}
        <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', borderRight: 1, borderColor: 'divider' }}>
          <PanelHeader title={t('panels.pageSelection')} />

          {/* Barre d'outils : recherche + filtre par statut de transcription */}
          <Box sx={{ px: 1.5, py: 1, borderBottom: 1, borderColor: 'divider', display: 'flex', gap: 1, flexShrink: 0, flexWrap: 'wrap' }}>
            <TextField
              size="small"
              sx={{ flex: 1, minWidth: 200 }}
              placeholder={t('filterPlaceholder')}
              value={treeFilter}
              onChange={(e) => setTreeFilter(e.target.value)}
              slotProps={{
                input: {
                  startAdornment: (
                    <InputAdornment position="start">
                      <SearchIcon fontSize="small" />
                    </InputAdornment>
                  ),
                  endAdornment: treeFilter ? (
                    <InputAdornment position="end">
                      <IconButton size="small" onClick={() => setTreeFilter('')} edge="end">
                        <ClearIcon fontSize="small" />
                      </IconButton>
                    </InputAdornment>
                  ) : undefined,
                },
              }}
            />
            <Tooltip
              title={selectedOcrModel
                ? t('filterTooltip.enabled')
                : t('filterTooltip.disabled')}
              arrow
            >
              <ToggleButtonGroup
                size="small"
                exclusive
                value={statusFilter}
                onChange={(_e, v) => { if (v) setStatusFilter(v); }}
                sx={{ height: 40 }}
              >
                <ToggleButton value="all" sx={{ whiteSpace: 'nowrap', textTransform: 'none' }}>
                  {t('statusFilter.all')}
                </ToggleButton>
                <ToggleButton value="missing" disabled={!selectedOcrModel} sx={{ whiteSpace: 'nowrap', textTransform: 'none' }}>
                  {t('statusFilter.missing')}
                </ToggleButton>
                <ToggleButton value="done" disabled={!selectedOcrModel} sx={{ whiteSpace: 'nowrap', textTransform: 'none' }}>
                  {t('statusFilter.done')}
                </ToggleButton>
              </ToggleButtonGroup>
            </Tooltip>
          </Box>

          <Box sx={{ flex: 1, overflow: 'auto' }}>
            {collectionsLoading ? (
              <Box sx={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 200, gap: 1.5 }}>
                <CircularProgress size={20} />
                <Typography variant="body2" color="text.secondary">{t('loading.collections')}</Typography>
              </Box>
            ) : collections.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
                {t('tree.noCollections')}
              </Typography>
            ) : visibleTree.length === 0 ? (
              <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
                {filterActive
                  ? t('tree.noResults', { query: treeFilter.trim() })
                  : statusFilter === 'missing'
                    ? t('tree.noMissing')
                    : t('tree.noDone')}
              </Typography>
            ) : (
              <List disablePadding>
                {visibleTree.map(({ collection, regs }) => {
                  const cKey = colKey(collection);
                  const isExpanded = filterActive || expandedCollections.has(cKey);
                  const allRegs = collection.registres || [];
                  const colCheck = getColCheckState(collection);

                  return (
                    <Box key={collection.id}>
                      <ListItemButton onClick={() => toggleCollection(cKey)} sx={{ py: 1 }}>
                        <ListItemIcon sx={{ minWidth: 36 }}>
                          <Checkbox
                            edge="start"
                            checked={colCheck.checked}
                            indeterminate={colCheck.indeterminate}
                            onClick={(e) => { e.stopPropagation(); toggleAllCollection(collection); }}
                            size="small"
                          />
                        </ListItemIcon>
                        <ListItemIcon sx={{ minWidth: 28 }}>
                          <FolderIcon fontSize="small" color="primary" />
                        </ListItemIcon>
                        <ListItemText
                          primary={collection.titre}
                          secondary={`${collection.type} — ${t('registres', { count: allRegs.length })} — ${t('pages', { count: allRegs.reduce((sum, r) => sum + r.pages_count, 0) })}`}
                        />
                        {(() => {
                          if (!selectedOcrModel) return null;
                          const done = allRegs.reduce((s, r) => s + (r.ocr_status?.[selectedOcrModel]?.pages_done ?? 0), 0);
                          const total = allRegs.reduce((s, r) => s + r.pages_count, 0);
                          if (total === 0) return null;
                          const complete = done >= total;
                          return (
                            <Tooltip title={complete ? t('tree.ocrComplete') : t('tree.pagesRemaining', { count: total - done })} arrow>
                              <Chip
                                label={`${fmtNum(done)} / ${fmtNum(total)}`}
                                color={complete ? 'success' : 'warning'}
                                variant="outlined"
                                size="small"
                                sx={{ mr: 1, cursor: 'default' }}
                                onClick={(e) => e.stopPropagation()}
                              />
                            </Tooltip>
                          );
                        })()}
                        {isExpanded ? <ExpandMoreIcon /> : <ChevronRightIcon />}
                      </ListItemButton>

                      <Collapse in={isExpanded} unmountOnExit>
                        {regs.length === 0 ? (
                          <Typography variant="body2" color="text.secondary" sx={{ pl: 9, py: 1 }}>
                            {t('tree.noRegistres')}
                          </Typography>
                        ) : (
                          <List disablePadding>
                            {regs.map((reg) => {
                              const rKey = `${cKey}/${reg.folder_name}`;
                              const isRegExpanded = expandedRegistres.has(rKey);
                              const regCheck = getRegCheckState(cKey, reg);
                              // Verrouillé par une tâche OCR (ce poste ou un autre) sur le même
                              // modèle : reste consultable, mais plus sélectionnable.
                              const busyFrom = busyRegistres.get(rKey);

                              return (
                                <Box key={reg.id} sx={{ pl: 3 }}>
                                  <ListItemButton onClick={() => toggleRegistre(rKey)} sx={{ py: 0.5 }}>
                                    <ListItemIcon sx={{ minWidth: 36 }}>
                                      <Checkbox
                                        edge="start"
                                        checked={regCheck.checked}
                                        indeterminate={regCheck.indeterminate}
                                        disabled={busyFrom !== undefined}
                                        onClick={(e) => { e.stopPropagation(); toggleAllPages(cKey, reg); }}
                                        size="small"
                                      />
                                    </ListItemIcon>
                                    <ListItemIcon sx={{ minWidth: 28 }}>
                                      <BookIcon fontSize="small" color="action" />
                                    </ListItemIcon>
                                    <ListItemText
                                      primary={reg.titre}
                                      secondary={t('pages', { count: reg.pages_count })}
                                      sx={busyFrom !== undefined ? { color: 'text.disabled' } : undefined}
                                    />
                                    {busyFrom !== undefined && (
                                      <Tooltip title={busyLabel(busyFrom)} arrow>
                                        <Chip
                                          size="small"
                                          color="warning"
                                          variant="outlined"
                                          icon={<HourglassEmptyIcon />}
                                          label={busyFrom || t('tree.busyChip')}
                                          sx={{ mr: 0.5, cursor: 'default' }}
                                          onClick={(e) => e.stopPropagation()}
                                        />
                                      </Tooltip>
                                    )}
                                    {(() => {
                                      if (!selectedOcrModel) return null;
                                      const done = reg.ocr_status?.[selectedOcrModel]?.pages_done ?? 0;
                                      const total = reg.pages_count;
                                      const complete = done >= total;
                                      const tooltip = done === 0
                                        ? t('tree.noTranscription')
                                        : complete
                                          ? t('tree.ocrComplete')
                                          : t('tree.pagesRemaining', { count: total - done });
                                      return (
                                        <Tooltip title={tooltip} arrow>
                                          <Chip
                                            size="small"
                                            label={`${done}/${total}`}
                                            color={done === 0 ? 'default' : complete ? 'success' : 'warning'}
                                            variant="outlined"
                                            sx={{ mr: 0.5, cursor: 'default' }}
                                            onClick={(e) => e.stopPropagation()}
                                          />
                                        </Tooltip>
                                      );
                                    })()}
                                    {isRegExpanded ? <ExpandMoreIcon fontSize="small" /> : <ChevronRightIcon fontSize="small" />}
                                  </ListItemButton>

                                  <Collapse in={isRegExpanded} unmountOnExit>
                                    {isRegExpanded && (() => {
                                      const allPages = pagesCache.get(rKey);
                                      const doneSet = doneCache.get(`${rKey}/${selectedOcrModel}`);
                                      // Les noms viennent du disque (et le statut par page du
                                      // doneSet) : tant que l'un des deux manque, on attend.
                                      if (!allPages || (statusFilter !== 'all' && !doneSet)) {
                                        return (
                                          <Box sx={{ display: 'flex', alignItems: 'center', gap: 1, pl: 9, py: 0.5 }}>
                                            <CircularProgress size={12} />
                                            <Typography variant="body2" color="text.secondary">{t('tree.loadingStates')}</Typography>
                                          </Box>
                                        );
                                      }
                                      const pages = statusFilter === 'all' || !doneSet
                                        ? allPages
                                        : allPages.filter((p) => statusFilter === 'done'
                                            ? doneSet.has(pageStem(p))
                                            : !doneSet.has(pageStem(p)));
                                      return pages.length === 0 ? (
                                        <Typography variant="body2" color="text.secondary" sx={{ pl: 9, py: 0.5 }}>
                                          {statusFilter === 'done' ? t('tree.noPageDone') : statusFilter === 'missing' ? t('tree.noPageMissing') : t('tree.noPage')}
                                        </Typography>
                                      ) : (
                                        <List disablePadding>
                                          {pages.map((page) => {
                                            const pKey = pageKey(cKey, reg.folder_name, page);
                                            const isDone = doneSet?.has(pageStem(page));
                                            // Une page est cochée soit à l'unité, soit parce que
                                            // son registre l'est en entier.
                                            const pageChecked = selectedRegistres.has(rKey) || selectedPages.has(pKey);
                                            return (
                                              <ListItemButton
                                                key={page}
                                                sx={{ pl: 6, py: 0.25 }}
                                                dense
                                                disabled={busyFrom !== undefined}
                                                onClick={() => togglePage(rKey, page, pages)}
                                              >
                                                <ListItemIcon sx={{ minWidth: 36 }}>
                                                  <Checkbox edge="start" checked={pageChecked} size="small" />
                                                </ListItemIcon>
                                                <ListItemIcon sx={{ minWidth: 28 }}>
                                                  <ImageIcon fontSize="small" sx={{ color: 'text.disabled' }} />
                                                </ListItemIcon>
                                                <ListItemText
                                                  primary={page}
                                                  slotProps={{ primary: { variant: 'body2' } }}
                                                />
                                                {doneSet && (
                                                  <Tooltip title={isDone ? t('page.transcribedWith') : t('page.notTranscribed')} arrow>
                                                    <CircleIcon sx={{ fontSize: 10, mr: 1, color: isDone ? 'success.main' : 'action.disabled' }} />
                                                  </Tooltip>
                                                )}
                                              </ListItemButton>
                                            );
                                          })}
                                        </List>
                                      );
                                    })()}
                                  </Collapse>
                                </Box>
                              );
                            })}
                          </List>
                        )}
                      </Collapse>
                    </Box>
                  );
                })}
              </List>
            )}
          </Box>
        </Box>

        {/* Colonne droite : modèles */}
        <Box sx={{ width: 360, flexShrink: 0, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>

          {/* Segmentation */}
          <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', borderBottom: 1, borderColor: 'divider' }}>
            <PanelHeader title={t('panels.segModel')} />
            <Box sx={{ flex: 1, overflow: 'auto', px: 1, py: 1 }}>
              {modelRadioGroup('segmentation', selectedSegModel, setSelectedSegModel)}
            </Box>
          </Box>

          {/* OCR */}
          <Box sx={{ flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
            <PanelHeader title={t('panels.ocrModel')} />
            <Box sx={{ flex: 1, overflow: 'auto', px: 1, py: 1 }}>
              {modelRadioGroup('ocr', selectedOcrModel, setSelectedOcrModel)}
            </Box>
          </Box>

        </Box>
      </Box>
      )}

      {/* ─── Confirmation d'ajout à la file ─── */}
      {queuedNotice !== null && (
        <Alert
          severity="success"
          sx={{ mt: 1, flexShrink: 0 }}
          onClose={() => setQueuedNotice(null)}
          action={
            <Button color="inherit" size="small" onClick={() => navigate('/tasks')}>
              {t('viewTasks')}
            </Button>
          }
        >
          {t('queued', { count: queuedNotice })}
        </Alert>
      )}

      {/* ─── Pages écartées faute d'image sur le disque ─── */}
      {skippedNotice > 0 && (
        <Alert severity="warning" sx={{ mt: 1, flexShrink: 0 }} onClose={() => setSkippedNotice(0)}>
          {t('skipped', { count: skippedNotice })}
        </Alert>
      )}

      {/* ─── Sélection portant sur un registre verrouillé ailleurs ─── */}
      {blockedSelection.size > 0 && (
        <Alert
          severity="warning"
          sx={{ mt: 1, flexShrink: 0 }}
          action={
            <Button color="inherit" size="small" onClick={dropBlockedSelection}>
              {t('busySelection.remove')}
            </Button>
          }
        >
          <Typography variant="body2" fontWeight={600}>
            {t('busySelection.title', { count: blockedSelection.size })}
          </Typography>
          <Typography variant="caption" color="text.secondary">
            {[...blockedSelection].map(([rKey, from]) => `${rKey} — ${busyLabel(from)}`).join(' · ')}
          </Typography>
        </Alert>
      )}

      {/* ─── Barre de lancement ─── */}
      <OcrLaunchBar
        selectedCount={selectedTotal}
        selectedRegistreCount={selectedRegistreCount}
        segModelName={models.find((m) => m.id === selectedSegModel)?.name ?? null}
        ocrModelName={models.find((m) => m.id === selectedOcrModel)?.name ?? null}
        estimateSeconds={estimateSeconds}
        activeOcr={activeOcr}
        pausingOcr={pausingOcr}
        launching={launching}
        canLaunch={canLaunch}
        disabledReason={disabledReason}
        envChip={<EnvStatusChip requirements={requirements} loading={reqLoading} failed={reqFailed} />}
        onLaunch={requestLaunch}
        onClearSelection={clearSelection}
        onPauseOcr={pauseOcr}
        onResumeOcr={resumeOcr}
      />

      {/* ─── Confirmation d'écrasement ─── */}
      <Dialog open={overwrite !== null} onClose={() => setOverwrite(null)} maxWidth="sm" fullWidth>
        <DialogTitle>{t('overwrite.title')}</DialogTitle>
        <DialogContent>
          <DialogContentText>
            {t('overwrite.detail', {
              count: overwrite?.doneCount ?? 0,
              total: overwrite?.total ?? 0,
              model: models.find((m) => m.id === selectedOcrModel)?.name ?? selectedOcrModel,
            })}
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setOverwrite(null)}>{t('common:actions.cancel')}</Button>
          {(overwrite?.missingTotal ?? 0) > 0 && (
            <Button
              onClick={() => {
                const o = overwrite!;
                setOverwrite(null);
                // Les périmètres repartent avec le filtre 'missing' : c'est le backend qui
                // écarte leurs pages déjà transcrites, sans rapatrier un seul nom.
                doLaunch(o.missingPages, o.scopes, 'missing');
              }}
            >
              {t('overwrite.missingOnly', { count: overwrite?.missingTotal ?? 0 })}
            </Button>
          )}
          <Button
            variant="contained"
            color="warning"
            onClick={() => {
              const o = overwrite!;
              setOverwrite(null);
              doLaunch(o.pages, o.scopes, statusFilter);
            }}
          >
            {t('overwrite.confirm')}
          </Button>
        </DialogActions>
      </Dialog>

      {/* ─── Création de la tâche : overlay bloquant ───
          Sur des dizaines de milliers de pages, le sondage des transcriptions puis la
          vérification des images côté backend prennent longtemps : sans overlay, la page
          paraît inerte et l'utilisateur reclique. */}
      <Backdrop
        open={launching}
        sx={{ zIndex: (theme) => theme.zIndex.modal + 1, color: '#fff', flexDirection: 'column', gap: 2 }}
      >
        <CircularProgress color="inherit" />
        <Typography variant="h6">
          {t(launchPhase === 'checking' ? 'launchOverlay.checking' : 'launchOverlay.creating')}
        </Typography>
        <Typography variant="body2" sx={{ opacity: 0.85, textAlign: 'center', px: 2 }}>
          {launchPhase === 'checking'
            ? (launchProgress.total > 0
                ? t('launchOverlay.checkingProgress', {
                    done: fmtNum(launchProgress.done),
                    total: fmtNum(launchProgress.total),
                  })
                : '')
            : t('launchOverlay.creatingDetail', { count: launchPageCount })}
        </Typography>
      </Backdrop>

    </Box>
  );
}
