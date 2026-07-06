# JAZ Platform - Product Requirements Document

## Original Problem Statement
JAZ Platform - منصة سحابية (Cloud SaaS) لإدارة الموظفين والشركات. توفر نظام متكامل يساعد أصحاب الشركات على إدارة الموظفين، الحضور، المهام، التقارير، والتواصل الداخلي.

## Architecture
- **Backend**: FastAPI + MongoDB (Motor async)
- **Frontend**: React 19 + Tailwind CSS + Shadcn UI + recharts
- **Auth**: JWT + bcrypt
- **QR**: qrcode (backend) + html5-qrcode (frontend)
- **Payments**: Stripe via emergentintegrations
- **Design**: Swiss High-Contrast, IBM Plex Sans Arabic, full RTL/LTR

## User Roles
1. **Super Admin** - Manages companies, plans, statistics
2. **Company Owner** - Manages employees, tasks, subscribes to plans
3. **Employee** - Tasks, QR attendance, performance, reports

## Implementation Status

### Phase 1 (MVP - Feb 2026) ✅
- JWT authentication with 3 roles
- Login page + role-based dashboards
- Basic backend CRUD for all entities
- RTL/LTR language toggle

### Phase 2 (Feature Complete - Feb 2026) ✅
**Owner UI (Complete):**
- Employees CRUD (Add/Edit/Delete with dialogs)
- Tasks CRUD (with priority, status, due dates, proof requirement, assignee)
- Departments CRUD
- Attendance viewing with GPS map links (OpenStreetMap)
- Reports viewing with images/files + CSV export
- Subscription page with Stripe checkout

**Employee UI (Complete):**
- Tasks page with status updates + proof file upload (base64)
- Attendance with QR Code Scanner (html5-qrcode) + GPS + history
- Performance page with Pie & Bar charts (recharts)
- Reports submission with multi image/file upload

**Super Admin UI (Complete):**
- Companies CRUD with add form (creates owner + QR)
- Subscription Plans CRUD

**Stripe Integration ✅:**
- Server-side plan pricing (secure)
- Checkout session creation
- Payment status polling
- Webhook endpoint
- Auto-activates company subscription on paid

**Security ✅:**
- Field whitelisting on PUT endpoints
- RBAC on all sensitive routes
- Password hashing with bcrypt

### Test Credentials
- Super Admin: `admin@jaz.com` / `admin123`
- Company Owner: `owner@demo.com` / `owner123`
- Employee: `employee1@demo.com` / `emp123`

### Test Results
Backend: 100% (38/38 tests passing)
Frontend: 100% (forms + navigation working)

## Backlog (Optional Future Enhancements)
- P2: AI-powered performance insights (Claude Sonnet 4.5)
- P2: Refactor server.py into modular routers
- P2: Add email/SMS notifications
- P3: Dark mode
- P3: Advanced analytics with time-series
