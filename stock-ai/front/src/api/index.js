import axios from 'axios';

const api = axios.create({ baseURL: '/api', timeout: 30000 });

export const getHistory = (code, days = 240, freq = 'day') =>
  api.get(`/history/${code}?days=${days}&freq=${freq}`).then(r => r.data);

export const analyzeStock = (code) =>
  api.post('/analyze', { code }).then(r => r.data);

export const getStock = (code) =>
  api.get(`/stock/${code}`).then(r => r.data);

export const getMarketStatus = () =>
  api.get('/market-status').then(r => r.data);

export const getIndices = () =>
  api.get('/indices').then(r => r.data);

export const getPortfolio = () =>
  api.get('/portfolio').then(r => r.data);

export const getOrders = () =>
  api.get('/orders').then(r => r.data);

export const placeOrder = (payload) =>
  api.post('/order', payload).then(r => r.data);

export const getIndicators = (code) =>
  api.post('/indicators', { code }).then(r => r.data);

export const getHotStocks = () =>
  api.get('/hot-stocks').then(r => r.data);

export const getSignal = (code) =>
  api.post('/signal', { code }).then(r => r.data);

export const getBotModel = () =>
  api.get('/bot-model').then(r => r.data);

export const setBotModel = (model) =>
  api.post('/bot-model/set', { model }).then(r => r.data);
