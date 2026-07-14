# Graph Report - .  (2026-07-14)

## Corpus Check
- cluster-only mode — file stats not available

## Summary
- 1378 nodes · 2071 edges · 175 communities (88 shown, 87 thin omitted)
- Extraction: 96% EXTRACTED · 4% INFERRED · 0% AMBIGUOUS · INFERRED: 77 edges (avg confidence: 0.69)
- Token cost: 0 input · 0 output

## Graph Freshness
- Built from commit: `64e78fc5`
- Run `git rev-parse HEAD` and compare to check if the graph is stale.
- Run `graphify update .` after code changes (no API cost).

## Community Hubs (Navigation)
- admin.py
- server.py
- tasks.py
- TestSmartCalendar
- AsyncSession
- resolutions
- TestWorkMessaging
- App.js
- use-toast.js
- require_company_member
- devDependencies
- BaseModel
- models.py
- TestSmartQRAttendance
- write_calendar_activity
- create_message
- update_calendar_event
- components.json
- menubar.jsx
- date
- validate_qr_attendance
- backend_test.py
- Task
- conftest.py
- Attendance.js
- dependencies
- TestCriticalTaskAlert
- Company
- Calendar.js
- DailyTask
- create_calendar_event
- require_calendar_access
- TestCompanyHolidayManagement
- command.jsx
- context-menu.jsx
- dropdown-menu.jsx
- Tasks.js
- WorkMessages.js
- package.json
- development
- alert-dialog.jsx
- table.jsx
- Tasks.js
- database.py
- subscription_plans.py
- breadcrumb.jsx
- drawer.jsx
- navigation-menu.jsx
- pagination.jsx
- select.jsx
- sheet.jsx
- toast.jsx
- CalendarMonitor.js
- CommunicationCenter.js
- TaskAttachment
- notifications.py
- Request
- TestRBAC
- compilerOptions
- WebpackHealthPlugin
- card.jsx
- dialog.jsx
- Companies.js
- health-endpoints.js
- CompanyHolidays.js
- check_conflicts_endpoint
- deliver_due_calendar_reminders
- TestAdmin
- TestAttendance
- TestAuth
- TestEmployeeTasks
- TestOwnerEmployeeCRUD
- craco.config.js
- alert.jsx
- input-otp.jsx
- auth.js
- compute_delivery_progress
- departments.py
- TestOwnerTaskCRUD
- html5-qrcode
- CriticalTaskAlert.js
- accordion.jsx
- avatar.jsx
- tabs.jsx
- toggle-group.jsx
- CreateCriticalTaskDialog.js
- CreateUrgentTaskDialog.js
- badge.jsx
- button.jsx
- label.jsx
- radio-group.jsx
- scroll-area.jsx
- toggle.jsx
- index.js
- axios
- class-variance-authority
- clsx
- cmdk
- date-fns
- dayjs
- embla-carousel-react
- framer-motion
- @hookform/resolvers
- html2canvas
- input-otp
- leaflet
- lodash
- lucide-react
- next-themes
- @radix-ui/react-accordion
- @radix-ui/react-alert-dialog
- @radix-ui/react-aspect-ratio
- @radix-ui/react-avatar
- @radix-ui/react-checkbox
- @radix-ui/react-collapsible
- @radix-ui/react-context-menu
- @radix-ui/react-dialog
- @radix-ui/react-dropdown-menu
- @radix-ui/react-label
- @radix-ui/react-menubar
- @radix-ui/react-popover
- @radix-ui/react-progress
- @radix-ui/react-scroll-area
- @radix-ui/react-select
- @radix-ui/react-separator
- @radix-ui/react-slider
- @radix-ui/react-slot
- @radix-ui/react-switch
- @radix-ui/react-toggle
- @radix-ui/react-toggle-group
- @radix-ui/react-tooltip
- react-day-picker
- react-dom
- react-hook-form
- react-leaflet
- react-qr-code
- react-resizable-panels
- react-router-dom
- react-scripts
- recharts
- sonner
- swr
- tailwind-merge
- tailwindcss-animate
- vaul
- zod
- checkbox.jsx
- hover-card.jsx
- input.jsx
- popover.jsx
- progress.jsx
- separator.jsx
- slider.jsx
- switch.jsx
- textarea.jsx
- tooltip.jsx
- testIds.js
- HOME

## God Nodes (most connected - your core abstractions)
1. `TestSmartCalendar` - 48 edges
2. `resolutions` - 42 edges
3. `TestWorkMessaging` - 38 edges
4. `Base` - 27 edges
5. `parse_uuid()` - 27 edges
6. `TestSmartQRAttendance` - 25 edges
7. `require_company_member()` - 23 edges
8. `require_calendar_access()` - 17 edges
9. `User` - 16 edges
10. `TimestampMixin` - 14 edges

## Surprising Connections (you probably didn't know these)
- `CalendarAttachment` --uses--> `Base`  [INFERRED]
  backend/models.py → backend/database.py
- `CalendarEventException` --uses--> `Base`  [INFERRED]
  backend/models.py → backend/database.py
- `CalendarEventParticipant` --uses--> `Base`  [INFERRED]
  backend/models.py → backend/database.py
- `CalendarEventReminder` --uses--> `Base`  [INFERRED]
  backend/models.py → backend/database.py
- `Company` --uses--> `Base`  [INFERRED]
  backend/models.py → backend/database.py

## Import Cycles
- None detected.

## Communities (175 total, 87 thin omitted)

### Community 0 - "admin.py"
Cohesion: 0.06
Nodes (72): User, count_employees(), count_employees_by_company(), create(), email_taken(), employee_stats_by_company(), get_by_email(), get_by_email_or_phone() (+64 more)

### Community 1 - "server.py"
Cohesion: 0.04
Nodes (48): AttachmentType, AttendanceCheckOut, AttendanceManualEdit, AttendanceResponseStatus, AttendanceStatus, check_out(), compute_star_rating(), create_employee() (+40 more)

### Community 2 - "tasks.py"
Cohesion: 0.08
Nodes (50): list_employee_ids(), list_employee_ids_for_templates(), AsyncSession, Batched version of list_employee_ids for a list view - one query,     grouped by, replace_for_template(), _bucket(), classify_attachment_type(), decode_and_validate() (+42 more)

### Community 4 - "AsyncSession"
Cohesion: 0.06
Nodes (44): activate_subscription(), cancel_task(), CompanyCreate, CompanyOwnerUpdate, complete_task(), create_company(), create_subscription_plan(), delete_company() (+36 more)

### Community 5 - "resolutions"
Cohesion: 0.05
Nodes (42): resolutions, **/anymatch/picomatch, **/axios/form-data, @babel/plugin-transform-modules-systemjs, **/cosmiconfig/yaml, **/css-loader/postcss, **/css-minimizer-webpack-plugin/postcss, **/cssnano/yaml (+34 more)

### Community 7 - "App.js"
Cohesion: 0.09
Nodes (17): EmployeeDashboard(), COLORS, EmployeePerformance(), EmployeeReports(), LoginPage(), OwnerDashboard(), STATUS_COLORS, WEEKDAY_LABELS (+9 more)

### Community 8 - "use-toast.js"
Cohesion: 0.08
Nodes (27): react, Carousel, CarouselContent, CarouselContext, CarouselItem, CarouselNext, CarouselPrevious, useCarousel() (+19 more)

### Community 9 - "require_company_member"
Cohesion: 0.11
Nodes (30): accept_message(), apply_common_filters(), archive_message(), close_message(), complete_message(), create_reminder(), get_accessible_message(), get_archived() (+22 more)

### Community 10 - "devDependencies"
Cohesion: 0.07
Nodes (29): autoprefixer, @babel/plugin-proposal-private-property-in-object, @craco/craco, @emergentbase/visual-edits, eslint, @eslint/js, eslint-plugin-import, eslint-plugin-jsx-a11y (+21 more)

### Community 11 - "BaseModel"
Cohesion: 0.08
Nodes (26): AttendanceResponse, CalendarEventUpdate, CompanyResponse, create_daily_task(), create_department(), create_report(), create_urgent_task(), DailyTaskCreate (+18 more)

### Community 12 - "models.py"
Cohesion: 0.16
Nodes (22): Base, Attendance, AuditLog, CalendarAttachment, CalendarEvent, CalendarEventException, CalendarEventParticipant, CalendarEventReminder (+14 more)

### Community 14 - "write_calendar_activity"
Cohesion: 0.13
Nodes (24): add_calendar_participants(), AttachmentUpload, cancel_calendar_event(), classify_attachment_type(), deactivate_company_holiday(), FinalAttendanceUpdate, get_event_for_edit(), mark_final_attendance() (+16 more)

### Community 15 - "create_message"
Cohesion: 0.15
Nodes (21): create_message(), deliver_message(), enrich_messages(), forward_message(), MessageCreate, MessageReply, MessageResponse, MessageUpdate (+13 more)

### Community 16 - "update_calendar_event"
Cohesion: 0.18
Nodes (20): build_event_detail(), calendar_owner_monitor(), calendar_paginate(), check_calendar_conflicts(), check_holiday_conflict(), combine_event_datetime(), compute_display_status(), count_raw_occurrences_before() (+12 more)

### Community 17 - "components.json"
Cohesion: 0.11
Nodes (17): aliases, components, hooks, lib, ui, utils, iconLibrary, rsc (+9 more)

### Community 18 - "menubar.jsx"
Cohesion: 0.12
Nodes (10): Menubar, MenubarCheckboxItem, MenubarContent, MenubarItem, MenubarLabel, MenubarRadioItem, MenubarSeparator, MenubarSubContent (+2 more)

### Community 19 - "date"
Cohesion: 0.17
Nodes (16): annotate_holiday_dates(), compute_calendar_dashboard_widgets(), get_attendance(), get_company_working_hours(), get_day_off_info(), get_employee_dashboard(), get_owner_dashboard(), get_working_hours() (+8 more)

### Community 20 - "validate_qr_attendance"
Cohesion: 0.14
Nodes (16): attendance_settings_for(), AttendanceCheckIn, AttendanceSettingsUpdate, check_in(), detect_fake_gps(), ensure_qr_token(), generate_qr_code(), get_attendance_settings() (+8 more)

### Community 21 - "backend_test.py"
Cohesion: 0.12
Nodes (6): JAZ Platform backend tests - Phase 2 CRUD, Employee flows, Stripe integration &, TestOwnerDepartment, TestOwnerSubscription, TestPerformance, TestReports, TestStripe

### Community 22 - "Task"
Cohesion: 0.29
Nodes (14): Task, append_proof_file(), claim_alert_delivery(), create(), get_daily_instance(), get_for_employee(), get_in_company(), get_pending_critical_for_employee() (+6 more)

### Community 23 - "conftest.py"
Cohesion: 0.21
Nodes (11): admin_headers(), admin_token(), auth_headers(), employee_headers(), employee_token(), employee_user(), _login(), owner_headers() (+3 more)

### Community 24 - "Attendance.js"
Cohesion: 0.22
Nodes (12): jspdf, buildQuery(), daysAgoStr(), formatDuration(), formatTime(), holidayBadge(), isoToLocalInput(), openMap() (+4 more)

### Community 25 - "dependencies"
Cohesion: 0.15
Nodes (14): cra-template, dependencies, cra-template, @radix-ui/react-hover-card, @radix-ui/react-navigation-menu, @radix-ui/react-radio-group, @radix-ui/react-tabs, @radix-ui/react-toast (+6 more)

### Community 27 - "Company"
Cohesion: 0.36
Nodes (10): Company, count_by_plan(), create(), detach_plan_from_deleted_companies(), get_by_id(), list_all(), AsyncSession, Soft-deleted companies keep their row (and their FK to the plan),     which woul (+2 more)

### Community 28 - "Calendar.js"
Cohesion: 0.20
Nodes (10): CalendarPage(), CATEGORY_LABELS, emptyCompose(), LOCATION_TYPE_LABELS, PRIORITY_ACCENT, PRIORITY_LABELS, RECURRENCE_LABELS, REMINDER_PRESET_LABELS (+2 more)

### Community 29 - "DailyTask"
Cohesion: 0.38
Nodes (9): DailyTask, DailyTaskAssignee, New join table - today assigned_to is an embedded array on daily_tasks., create(), delete_by_id(), get_in_company(), list_active_for_employee(), list_by_company() (+1 more)

### Community 30 - "create_calendar_event"
Cohesion: 0.22
Nodes (10): CalendarEventCreate, create_calendar_event(), create_weekly_holiday_pattern(), next_calendar_reference_number(), next_date_for_weekday(), Same atomic find-and-increment pattern as messaging's reference     numbers, del, target_weekday_sunday0: 0=Sunday..6=Saturday, same convention as     working_hou, Creates one permanent (never-ending) weekly-recurring company_holiday     event (+2 more)

### Community 31 - "require_calendar_access"
Cohesion: 0.27
Nodes (10): create_calendar_reminder(), EventReminderCreate, EventResponseUpdate, get_accessible_calendar_event(), get_calendar_attachment(), get_calendar_event(), Read access: creator, an actual participant, the company Owner     (always - mat, Owner may set a reminder on any company event (oversight role) even     when not (+2 more)

### Community 33 - "command.jsx"
Cohesion: 0.20
Nodes (7): Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList, CommandSeparator

### Community 34 - "context-menu.jsx"
Cohesion: 0.20
Nodes (8): ContextMenuCheckboxItem, ContextMenuContent, ContextMenuItem, ContextMenuLabel, ContextMenuRadioItem, ContextMenuSeparator, ContextMenuSubContent, ContextMenuSubTrigger

### Community 35 - "dropdown-menu.jsx"
Cohesion: 0.20
Nodes (8): DropdownMenuCheckboxItem, DropdownMenuContent, DropdownMenuItem, DropdownMenuLabel, DropdownMenuRadioItem, DropdownMenuSeparator, DropdownMenuSubContent, DropdownMenuSubTrigger

### Community 36 - "Tasks.js"
Cohesion: 0.22
Nodes (8): EmployeeTasks(), playAlertSound(), PRIORITY_COLORS, PRIORITY_LABELS, STATUS_COLORS, STATUS_LABELS, t(), translations

### Community 37 - "WorkMessages.js"
Cohesion: 0.27
Nodes (8): CONFIDENTIALITY_LABELS, emptyCompose(), formatDateTime(), playAlertSound(), PRIORITY_CONFIG, STATUS_LABELS, SUGGESTED_TAGS, WorkMessages()

### Community 38 - "package.json"
Cohesion: 0.22
Nodes (8): name, packageManager, private, scripts, build, start, test, version

### Community 39 - "development"
Cohesion: 0.22
Nodes (9): browserslist, development, production, >0.2%, last 1 chrome version, last 1 firefox version, last 1 safari version, not dead (+1 more)

### Community 40 - "alert-dialog.jsx"
Cohesion: 0.22
Nodes (6): AlertDialogAction, AlertDialogCancel, AlertDialogContent, AlertDialogDescription, AlertDialogOverlay, AlertDialogTitle

### Community 41 - "table.jsx"
Cohesion: 0.22
Nodes (8): Table, TableBody, TableCaption, TableCell, TableFooter, TableHead, TableHeader, TableRow

### Community 42 - "Tasks.js"
Cohesion: 0.28
Nodes (8): formatTime(), getCategoryBadge(), OwnerTasks(), PRIORITY_COLORS, PRIORITY_LABELS_AR, STATUS_COLORS, STATUS_LABELS_AR, TaskTimeline()

### Community 43 - "database.py"
Cohesion: 0.32
Nodes (4): do_run_migrations(), run_migrations_online(), dotenv, dotenv

### Community 44 - "subscription_plans.py"
Cohesion: 0.54
Nodes (7): SubscriptionPlan, create(), delete_by_id(), get_by_id(), get_by_name(), list_all(), AsyncSession

### Community 45 - "breadcrumb.jsx"
Cohesion: 0.25
Nodes (5): Breadcrumb, BreadcrumbItem, BreadcrumbLink, BreadcrumbList, BreadcrumbPage

### Community 46 - "drawer.jsx"
Cohesion: 0.25
Nodes (4): DrawerContent, DrawerDescription, DrawerOverlay, DrawerTitle

### Community 47 - "navigation-menu.jsx"
Cohesion: 0.25
Nodes (7): NavigationMenu, NavigationMenuContent, NavigationMenuIndicator, NavigationMenuList, NavigationMenuTrigger, navigationMenuTriggerStyle, NavigationMenuViewport

### Community 49 - "select.jsx"
Cohesion: 0.25
Nodes (7): SelectContent, SelectItem, SelectLabel, SelectScrollDownButton, SelectScrollUpButton, SelectSeparator, SelectTrigger

### Community 50 - "sheet.jsx"
Cohesion: 0.25
Nodes (5): SheetContent, SheetDescription, SheetOverlay, SheetTitle, sheetVariants

### Community 51 - "toast.jsx"
Cohesion: 0.25
Nodes (7): Toast, ToastAction, ToastClose, ToastDescription, ToastTitle, toastVariants, ToastViewport

### Community 52 - "CalendarMonitor.js"
Cohesion: 0.29
Nodes (7): CalendarMonitor(), CATEGORY_LABELS, EVENT_LABELS, formatDateTime(), RESPONSE_COLORS, RESPONSE_LABELS, STATUS_LABELS

### Community 53 - "CommunicationCenter.js"
Cohesion: 0.29
Nodes (7): ATTACHMENT_TYPE_OPTIONS, CommunicationCenter(), EVENT_LABELS, formatDateTime(), PRIORITY_COLORS, PRIORITY_LABELS, STATUS_OPTIONS

### Community 54 - "TaskAttachment"
Cohesion: 0.48
Nodes (6): Metadata only - file bytes live in object storage (MinIO/S3)., TaskAttachment, create(), list_by_task(), list_by_tasks(), AsyncSession

### Community 55 - "notifications.py"
Cohesion: 0.29
Nodes (5): create(), AsyncSession, pending_critical_tasks(), AsyncSession, Critical Task Alert fallback detection, piggybacked on the heartbeat     (no sch

### Community 56 - "Request"
Cohesion: 0.29
Nodes (7): check_payment_status(), CheckoutRequest, create_checkout(), _db_unavailable_handler(), stripe_webhook(), Request, ServerSelectionTimeoutError

### Community 58 - "compilerOptions"
Cohesion: 0.33
Nodes (6): compilerOptions, baseUrl, paths, include, @/*, src/*

### Community 60 - "card.jsx"
Cohesion: 0.29
Nodes (6): Card, CardContent, CardDescription, CardFooter, CardHeader, CardTitle

### Community 61 - "dialog.jsx"
Cohesion: 0.29
Nodes (4): DialogContent, DialogDescription, DialogOverlay, DialogTitle

### Community 62 - "Companies.js"
Cohesion: 0.57
Nodes (6): formatCurrency(), formatLastSeen(), getRemainingDays(), getStatusBadge(), PlanCombobox(), SuperAdminCompanies()

### Community 63 - "health-endpoints.js"
Cohesion: 0.47
Nodes (5): formatBytes(), formatDuration(), os, SERVER_START_TIME, setupHealthEndpoints()

### Community 64 - "CompanyHolidays.js"
Cohesion: 0.40
Nodes (5): defaultWorkingHours, emptyForm, OwnerCompanyHolidays(), WEEKDAY_LABELS, weekdayOf()

### Community 65 - "check_conflicts_endpoint"
Cohesion: 0.40
Nodes (5): check_conflicts_endpoint(), ConflictCheckRequest, Fan-out at creation time, same snapshot-at-send convention already     used ever, Dry-run used by the compose UI before save - never mutates anything., resolve_calendar_recipients()

### Community 66 - "deliver_due_calendar_reminders"
Cohesion: 0.40
Nodes (5): deliver_due_calendar_reminders(), deliver_due_reminders(), get_notifications(), Same self-heal-on-read pattern as message reminders, deliberately a     separate, Self-heal on read, the same house pattern used for subscription     expiry/prese

### Community 72 - "craco.config.js"
Cohesion: 0.40
Nodes (3): config, path, webpackConfig

### Community 73 - "alert.jsx"
Cohesion: 0.40
Nodes (4): Alert, AlertDescription, AlertTitle, alertVariants

### Community 74 - "input-otp.jsx"
Cohesion: 0.40
Nodes (4): InputOTP, InputOTPGroup, InputOTPSeparator, InputOTPSlot

### Community 75 - "auth.js"
Cohesion: 0.40
Nodes (3): LOGIN, LOGOUT, REGISTER

### Community 76 - "compute_delivery_progress"
Cohesion: 0.50
Nodes (4): Any, compute_delivery_progress(), get_message_timeline(), Computed on read from message_recipients - never stored redundantly,     consist

### Community 77 - "departments.py"
Cohesion: 0.67
Nodes (3): create(), list_by_company(), AsyncSession

### Community 79 - "html5-qrcode"
Cohesion: 0.50
Nodes (3): html5-qrcode, EmployeeAttendance(), html5-qrcode

### Community 80 - "CriticalTaskAlert.js"
Cohesion: 0.83
Nodes (3): CriticalTaskAlert(), formatDateTime(), playCriticalAlertSound()

### Community 81 - "accordion.jsx"
Cohesion: 0.50
Nodes (3): AccordionContent, AccordionItem, AccordionTrigger

### Community 82 - "avatar.jsx"
Cohesion: 0.50
Nodes (3): Avatar, AvatarFallback, AvatarImage

### Community 83 - "tabs.jsx"
Cohesion: 0.50
Nodes (3): TabsContent, TabsList, TabsTrigger

### Community 84 - "toggle-group.jsx"
Cohesion: 0.50
Nodes (3): ToggleGroup, ToggleGroupContext, ToggleGroupItem

## Knowledge Gaps
- **367 isolated node(s):** `UserRole`, `TaskStatus`, `TaskPriority`, `TaskCategory`, `RecurrenceType` (+362 more)
  These have ≤1 connection - possible missing edges or undocumented components.
- **87 thin communities (<3 nodes) omitted from report** — run `graphify query` to explore isolated nodes.

## Suggested Questions
_Questions this graph is uniquely positioned to answer:_

- **Why does `devDependencies` connect `devDependencies` to `database.py`, `package.json`?**
  _High betweenness centrality (0.286) - this node is a cross-community bridge._
- **Why does `dotenv` connect `database.py` to `server.py`?**
  _High betweenness centrality (0.279) - this node is a cross-community bridge._
- **Why does `dotenv` connect `database.py` to `devDependencies`?**
  _High betweenness centrality (0.279) - this node is a cross-community bridge._
- **What connects `UserRole`, `TaskStatus`, `TaskPriority` to the rest of the system?**
  _367 weakly-connected nodes found - possible documentation gaps or missing edges._
- **Should `admin.py` be split into smaller, more focused modules?**
  _Cohesion score 0.05907172995780591 - nodes in this community are weakly interconnected._
- **Should `server.py` be split into smaller, more focused modules?**
  _Cohesion score 0.04149620105201637 - nodes in this community are weakly interconnected._
- **Should `tasks.py` be split into smaller, more focused modules?**
  _Cohesion score 0.07910014513788098 - nodes in this community are weakly interconnected._