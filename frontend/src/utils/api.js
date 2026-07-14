import axios from 'axios';

const API_URL = process.env.REACT_APP_BACKEND_URL + '/api';

const api = axios.create({
  baseURL: API_URL,
});

// Add token to requests
api.interceptors.request.use((config) => {
  const token = localStorage.getItem('token');
  if (token) {
    config.headers.Authorization = `Bearer ${token}`;
  }
  return config;
});

// If a company's subscription is expired/suspended, the backend rejects with
// a structured 403 (detail.field === 'subscription'). Instead of letting each
// page show a generic error, log out and redirect to the dedicated status
// page - this covers every owner/employee request app-wide from one place.
api.interceptors.response.use(
  (response) => response,
  (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail;
    if (status === 403 && detail && typeof detail === 'object' && detail.field === 'subscription') {
      localStorage.removeItem('token');
      localStorage.removeItem('role');
      const params = new URLSearchParams({
        status: detail.status || '',
        end_date: detail.subscription_end_date || ''
      });
      window.location.href = `/subscription-blocked?${params.toString()}`;
    } else if (status === 401) {
      // A stored token that the backend rejects (expired 24h TTL, malformed,
      // bad signature, or user removed) means the session is dead. App.js
      // treats any token string in localStorage as "logged in" and never
      // re-validates, so without this the whole app sits on 401s showing
      // "خطأ في جلب البيانات" on every page with no way to recover. Clear the
      // dead session and send the user to the login screen - one place,
      // app-wide, mirroring the 403-subscription handling above.
      //
      // A failed LOGIN attempt also returns 401, but at that point there is no
      // stored token yet, so `hadToken` is false and we let the login page
      // show its own "invalid credentials" message instead of redirecting.
      const hadToken = !!localStorage.getItem('token');
      const isLoginRequest = (error.config?.url || '').includes('/auth/login');
      if (hadToken && !isLoginRequest) {
        localStorage.removeItem('token');
        localStorage.removeItem('role');
        // Avoid a redirect loop if we're already on the login route.
        if (window.location.pathname !== '/') {
          window.location.href = '/';
        }
      }
    }
    return Promise.reject(error);
  }
);

export default api;