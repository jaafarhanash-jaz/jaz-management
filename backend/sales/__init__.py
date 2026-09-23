"""JAZ Sales - the internal Sales-management module for JAZ staff.

NOT a customer-facing CRM: only internal `jaz_staff` users (with assigned
staff roles) and Super Admins can reach anything in this package. Layering
mirrors the rest of the backend (router -> services -> repositories ->
models). This package may import core modules read-only; core modules never
import from here (server.py only mounts `sales.router.sales_router`).
"""
