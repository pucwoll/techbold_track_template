import axios from 'axios';

const api = axios.create({
  baseURL: import.meta.env.VITE_API_BASE || 'http://localhost:8000',
});

export const getTickets = async (status?: string, priority?: string, sort?: string) => {
  const params: any = {};
  if (status) params.status = status;
  if (priority) params.priority = priority;
  if (sort) params.sort = sort;
  const res = await api.get('/api/tickets/', { params });
  return res.data;
};

export const getTicketDetails = async (ticketId: number) => {
  const res = await api.get(`/api/tickets/${ticketId}`);
  return res.data;
};

export const getRunStatus = async (ticketId: number) => {
  const res = await api.get(`/api/tickets/${ticketId}/run`);
  return res.data;
};

export const startRun = async (ticketId: number) => {
  const res = await api.post(`/api/tickets/${ticketId}/run/start`);
  return res.data;
};

export const approveCommand = async (ticketId: number, command: string) => {
  const res = await api.post(`/api/tickets/${ticketId}/run/approve`, { command });
  return res.data;
};

export const rejectCommand = async (ticketId: number) => {
  const res = await api.post(`/api/tickets/${ticketId}/run/reject`);
  return res.data;
};

export const submitActivity = async (ticketId: number) => {
  const res = await api.post(`/api/tickets/${ticketId}/run/submit-activity`);
  return res.data;
};

export default api;