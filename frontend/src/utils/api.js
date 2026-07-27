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

// Concurrent 401s (several requests failing at once) must trigger only one
// refresh call, not one per request - every caller awaits this same promise.
let refreshPromise = null;

async function refreshAccessToken() {
  const refreshToken = localStorage.getItem('refresh_token');
  if (!refreshToken) {
    throw new Error('No refresh token available');
  }
  // A plain axios call, not the `api` instance - going through `api` would
  // re-enter this same response interceptor.
  const response = await axios.post(`${API_URL}/auth/refresh`, { refresh_token: refreshToken });
  localStorage.setItem('token', response.data.token);
  localStorage.setItem('refresh_token', response.data.refresh_token);
  return response.data.token;
}

function clearSessionAndRedirect() {
  localStorage.removeItem('token');
  localStorage.removeItem('refresh_token');
  localStorage.removeItem('role');
  // Avoid a redirect loop if we're already on the login route.
  if (window.location.pathname !== '/') {
    window.location.href = '/';
  }
}

// If a company's subscription is expired/suspended, the backend rejects with
// a structured 403 (detail.field === 'subscription'). Instead of letting each
// page show a generic error, log out and redirect to the dedicated status
// page - this covers every owner/employee request app-wide from one place.
api.interceptors.response.use(
  (response) => response,
  async (error) => {
    const status = error.response?.status;
    const detail = error.response?.data?.detail;
    const requestUrl = error.config?.url || '';
    const isLoginRequest = requestUrl.includes('/auth/login');
    const isRefreshRequest = requestUrl.includes('/auth/refresh');

    if (status === 403 && detail && typeof detail === 'object' && detail.field === 'subscription') {
      clearSessionAndRedirect();
      const params = new URLSearchParams({
        status: detail.status || '',
        end_date: detail.subscription_end_date || ''
      });
      window.location.href = `/subscription-blocked?${params.toString()}`;
      return Promise.reject(error);
    }

    if (status === 401 && !isLoginRequest && !isRefreshRequest) {
      // A stored token that the backend rejects (expired 1h TTL, malformed,
      // bad signature, or user removed) - try a silent refresh before
      // giving up, so a normal hour-boundary expiry doesn't interrupt the
      // user. A failed LOGIN attempt also returns 401, but at that point
      // there is no stored token yet, so isLoginRequest already excludes
      // it above and lets the login page show its own error instead.
      const hadToken = !!localStorage.getItem('token');
      const hasRefreshToken = !!localStorage.getItem('refresh_token');
      const alreadyRetried = error.config?._retriedAfterRefresh;

      if (hadToken && hasRefreshToken && !alreadyRetried) {
        try {
          if (!refreshPromise) {
            refreshPromise = refreshAccessToken().finally(() => {
              refreshPromise = null;
            });
          }
          const newToken = await refreshPromise;
          error.config._retriedAfterRefresh = true;
          error.config.headers.Authorization = `Bearer ${newToken}`;
          return api.request(error.config);
        } catch (refreshError) {
          // Refresh token itself is dead (expired/revoked/reused) - fall
          // through to the same dead-session cleanup as before.
        }
      }

      if (hadToken) {
        clearSessionAndRedirect();
      }
    }
    return Promise.reject(error);
  }
);

export default api;
