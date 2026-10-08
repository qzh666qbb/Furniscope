import {
  createContext,
  useCallback,
  useContext,
  useMemo,
  useRef,
  useState,
} from "react";
import { api } from "./api.js";

const MarketInsightsContext = createContext(null);
const CACHE_TTL_MS = 60_000;
const intelligenceKey = (datasetId) => datasetId ? String(datasetId) : "default";

export function MarketInsightsProvider({ children }) {
  const [datasets, setDatasets] = useState([]);
  const [datasetsLoading, setDatasetsLoading] = useState(false);
  const [datasetsError, setDatasetsError] = useState("");
  const [datasetsLoadedAt, setDatasetsLoadedAt] = useState(0);
  const [selectedDatasetId, setSelectedDatasetId] = useState("");
  const [cacheRevision, setCacheRevision] = useState(0);
  const datasetsRequest = useRef(null);
  const intelligenceCache = useRef(new Map());
  const intelligenceRequests = useRef(new Map());

  const refreshDatasets = useCallback(async ({ force = false } = {}) => {
    if (!force && datasetsLoadedAt && Date.now() - datasetsLoadedAt < CACHE_TTL_MS) {
      return datasets;
    }
    if (datasetsRequest.current) return datasetsRequest.current;
    setDatasetsLoading(true);
    setDatasetsError("");
    const request = api("/api/v1/market-datasets?page_size=100")
      .then((page) => {
        const items = page.items || [];
        setDatasets(items);
        setDatasetsLoadedAt(Date.now());
        setSelectedDatasetId((current) => {
          if (current && items.some((item) => String(item.dataset_id) === String(current) && item.status === "ready")) {
            return current;
          }
          return String(items.find((item) => item.status === "ready")?.dataset_id || "");
        });
        return items;
      })
      .catch((reason) => {
        setDatasetsError(reason instanceof Error ? reason.message : String(reason));
        throw reason;
      })
      .finally(() => {
        datasetsRequest.current = null;
        setDatasetsLoading(false);
      });
    datasetsRequest.current = request;
    return request;
  }, [datasets, datasetsLoadedAt]);

  const loadIntelligence = useCallback(async (datasetId, { force = false } = {}) => {
    const key = intelligenceKey(datasetId);
    const cached = intelligenceCache.current.get(key);
    if (!force && cached && Date.now() - cached.loadedAt < CACHE_TTL_MS) return cached.data;
    if (intelligenceRequests.current.has(key)) return intelligenceRequests.current.get(key);
    const query = datasetId ? `?dataset_id=${encodeURIComponent(datasetId)}` : "";
    const request = api(`/api/v1/market-intelligence/overview${query}`)
      .then((data) => {
        intelligenceCache.current.set(key, { data, loadedAt: Date.now() });
        setCacheRevision((value) => value + 1);
        return data;
      })
      .finally(() => intelligenceRequests.current.delete(key));
    intelligenceRequests.current.set(key, request);
    return request;
  }, []);

  const getIntelligence = useCallback((datasetId) => (
    intelligenceCache.current.get(intelligenceKey(datasetId))?.data || null
  ), [cacheRevision]);

  const updateIntelligence = useCallback((datasetId, updater) => {
    const key = intelligenceKey(datasetId);
    const cached = intelligenceCache.current.get(key);
    if (!cached) return;
    const data = typeof updater === "function" ? updater(cached.data) : updater;
    intelligenceCache.current.set(key, { data, loadedAt: Date.now() });
    setCacheRevision((value) => value + 1);
  }, []);

  const invalidateIntelligence = useCallback((datasetId) => {
    if (datasetId == null) intelligenceCache.current.clear();
    else intelligenceCache.current.delete(intelligenceKey(datasetId));
    setCacheRevision((value) => value + 1);
  }, []);

  const upsertDataset = useCallback((dataset) => {
    setDatasets((items) => {
      const exists = items.some((item) => item.dataset_id === dataset.dataset_id);
      return exists
        ? items.map((item) => item.dataset_id === dataset.dataset_id ? { ...item, ...dataset } : item)
        : [dataset, ...items];
    });
    setDatasetsLoadedAt(Date.now());
    invalidateIntelligence(dataset.dataset_id);
  }, [invalidateIntelligence]);

  const removeDataset = useCallback((datasetId) => {
    setDatasets((items) => items.filter((item) => item.dataset_id !== datasetId));
    setSelectedDatasetId((current) => String(current) === String(datasetId) ? "" : current);
    invalidateIntelligence(datasetId);
  }, [invalidateIntelligence]);

  const value = useMemo(() => ({
    datasets,
    datasetsLoading,
    datasetsLoaded: datasetsLoadedAt > 0,
    datasetsError,
    selectedDatasetId,
    setSelectedDatasetId,
    refreshDatasets,
    upsertDataset,
    removeDataset,
    loadIntelligence,
    getIntelligence,
    updateIntelligence,
    invalidateIntelligence,
    cacheRevision,
  }), [
    datasets,
    datasetsLoading,
    datasetsLoadedAt,
    datasetsError,
    selectedDatasetId,
    refreshDatasets,
    upsertDataset,
    removeDataset,
    loadIntelligence,
    getIntelligence,
    updateIntelligence,
    invalidateIntelligence,
    cacheRevision,
  ]);

  return <MarketInsightsContext.Provider value={value}>{children}</MarketInsightsContext.Provider>;
}

export function useMarketInsights() {
  const value = useContext(MarketInsightsContext);
  if (!value) throw new Error("useMarketInsights must be used within MarketInsightsProvider");
  return value;
}
