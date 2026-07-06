# JAZ Platform - Product Requirements Document

## Original Problem Statement
JAZ Platform - منصة سحابية (Cloud SaaS) لإدارة الموظفين والشركات. توفر نظام متكامل يساعد أصحاب الشركات على إدارة الموظفين، الحضور، المهام، التقارير، والتواصل الداخلي من مكان واحد.

## Architecture

### Tech Stack
- **Backend**: FastAPI + MongoDB (Motor async driver)
- **Frontend**: React 19 + Tailwind CSS + Shadcn UI
- **Auth**: JWT with bcrypt password hashing
- **Design**: Swiss & High-Contrast archetype with IBM Plex Sans Arabic font
- **RTL Support**: Full Arabic + English support with Tailwind logical properties

### User Roles
1. **Super Admin** - JAZ management (companies, subscriptions, statistics)
2. **Company Owner** - Manage employees, tasks, attendance, reports
3. **Employee** - View tasks, check-in/out via QR, submit reports, view performance

## Core Requirements
- Multi-role authentication with role-based access control
- Role-specific dashboards with real-time statistics
- Task management with priorities and status tracking
- QR Code-based attendance with GPS location
- Employee performance tracking
- Report submission and review
- Department management
- In-app notifications
- Subscription plans based on employee count
- Full Arabic (RTL) and English support

## What's Been Implemented (Feb 2026 - Initial MVP)

### Backend (server.py)
- ✅ JWT authentication with bcrypt
- ✅ Login endpoint (email or phone + password)
- ✅ Super Admin: statistics, companies CRUD, subscription plans CRUD
- ✅ Company Owner: dashboard, employees CRUD, tasks CRUD, attendance view, reports, departments
- ✅ Employee: dashboard, tasks view/update, attendance check-in/out with QR + GPS, performance metrics, reports submission, profile update
- ✅ Notifications system
- ✅ QR Code generation for companies
- ✅ Seed data endpoint with demo users

### Frontend
- ✅ Login page with RTL support and demo credentials display
- ✅ Super Admin Dashboard with statistics
- ✅ Super Admin Companies management page
- ✅ Company Owner Dashboard with stats and recent reports
- ✅ Employee Dashboard with tasks, attendance status, notifications
- ✅ Full Layout component with sidebar navigation for all roles
- ✅ Language toggle (Arabic RTL / English LTR)
- ✅ Protected routes with role-based access

### Test Credentials
- Super Admin: admin@jaz.com / admin123
- Company Owner: owner@demo.com / owner123
- Employee: employee1@demo.com / emp123

## Prioritized Backlog (Next Phases)

### P0 - Critical (Complete Core Flow)
- [ ] Full Employees management UI for Company Owner (add/edit/delete)
- [ ] Full Tasks management UI (create tasks with priority, assignment)
- [ ] Employee Tasks page with status updates and proof upload
- [ ] QR Code Scanner UI for employee check-in
- [ ] Attendance history view for employees
- [ ] Reports submission form for employees

### P1 - Important
- [ ] Departments management UI
- [ ] Attendance report for Company Owner
- [ ] Performance page with charts (recharts)
- [ ] Subscription plans management UI for Super Admin
- [ ] Companies add form with owner creation for Super Admin
- [ ] Notifications bell component with dropdown

### P2 - Nice to Have
- [ ] File/image upload for reports and task proofs (integrate object storage)
- [ ] GPS location display on map for attendance
- [ ] Export functionality for reports (CSV/PDF)
- [ ] Advanced analytics dashboard
- [ ] Payment gateway integration (Stripe/Razorpay) for automatic subscriptions
- [ ] Email/SMS notifications
- [ ] Dark mode
