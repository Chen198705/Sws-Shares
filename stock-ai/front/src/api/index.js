import axios from 'axios';

const api = axios.create({ baseURL: '/api', timeout: 20000 });
const aiApi = axios.create({ baseURL: '/api', timeout: 120000 });

export const getHistory = (code, days = 240, freq = 'day', signal) =>
  api.get(`/history/${code}?days=${days}&freq=${freq}`, { signal }).then(r => r.data);

export const analyzeStock = (code, signal) =>
  aiApi.post('/analyze', { code }, { signal }).then(r => r.data);

export const getStock = (code, signal) =>
  api.get(`/stock/${code}`, { signal }).then(r => r.data);

export const getMarketStatus = (signal) =>
  api.get('/market-status', { signal }).then(r => r.data);

export const getIndices = (signal) =>
  api.get('/indices', { signal }).then(r => r.data);

export const getPortfolio = (signal) =>
  api.get('/portfolio', { signal }).then(r => r.data);

export const getOrders = (signal) =>
  api.get('/orders', { signal }).then(r => r.data);

export const getOrderStats = (signal) =>
  api.get('/orders/stats', { signal }).then(r => r.data);

export const placeOrder = (payload) =>
  api.post('/order', payload).then(r => r.data);

export const getIndicators = (code) =>
  api.post('/indicators', { code }).then(r => r.data);

export const getHotStocks = (signal) =>
  api.get('/hot-stocks', { signal }).then(r => r.data);

export const getSignal = (code, signal) =>
  api.post('/signal', { code }, { signal }).then(r => r.data);

export const getBotModel = (signal) =>
  api.get('/bot-model', { signal }).then(r => r.data);

export const setBotModel = (model) =>
  api.post('/bot-model/set', { model }).then(r => r.data);

export const getReconcile = (signal) =>
  api.get('/reconcile', { signal }).then(r => r.data);

export const getStrategyParams = (signal) =>
  api.get('/strategy-params', { signal }).then(r => r.data);
