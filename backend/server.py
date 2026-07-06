from fastapi import FastAPI, APIRouter, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from dotenv import load_dotenv
from starlette.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
import os
import logging
from pathlib import Path
from pydantic import BaseModel, Field, ConfigDict, EmailStr
from typing import List, Optional, Dict, Any
import uuid
from datetime import datetime, timezone, timedelta
from passlib.context import CryptContext
from jose import JWTError, jwt
import qrcode
from io import BytesIO
import base64

ROOT_DIR = Path(__file__).parent
load_dotenv(ROOT_DIR / '.env')

# MongoDB connection
mongo_url = os.environ['MONGO_URL']
client = AsyncIOMotorClient(mongo_url)
db = client[os.environ['DB_NAME']]

# Security
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")
security = HTTPBearer()
SECRET_KEY = os.environ.get('JWT_SECRET_KEY', 'your-secret-key-change-in-production')
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

# Create the main app
app = FastAPI()
api_router = APIRouter(prefix="/api")

# ============ Models ============

class UserRole:
    SUPER_ADMIN = "super_admin"
    COMPANY_OWNER = "company_owner"
    EMPLOYEE = "employee"

class TaskStatus:
    NEW = "new"
    IN_PROGRESS = "in_progress"
    PENDING_REVIEW = "pending_review"
    COMPLETED = "completed"
    REJECTED = "rejected"
    OVERDUE = "overdue"

class TaskPriority:
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

class AttendanceStatus:
    PRESENT = "present"
    LATE = "late"
    ABSENT = "absent"

# ============ Request/Response Models ============

class LoginRequest(BaseModel):
    email_or_phone: str
    password: str

class LoginResponse(BaseModel):
    token: str
    user: Dict[str, Any]
    role: str

class UserCreate(BaseModel):
    email: EmailStr
    phone: str
    password: str
    name: str
    role: str
    company_id: Optional[str] = None
    department: Optional[str] = None
    position: Optional[str] = None

class UserResponse(BaseModel):
    id: str
    email: str
    phone: str
    name: str
    role: str
    company_id: Optional[str] = None
    avatar: Optional[str] = None
    status: str = "active"
    department: Optional[str] = None
    position: Optional[str] = None

class CompanyCreate(BaseModel):
    name: str
    owner_email: EmailStr
    owner_name: str
    owner_password: str
    owner_phone: str
    address: Optional[str] = None
    subscription_plan_id: Optional[str] = None

class CompanyResponse(BaseModel):
    id: str
    name: str
    owner_id: str
    qr_code: str
    subscription_status: str
    subscription_plan_id: Optional[str] = None
    subscription_end_date: Optional[str] = None
    address: Optional[str] = None
    created_at: str
    employee_count: int = 0

class SubscriptionPlanCreate(BaseModel):
    name: str
    max_employees: int
    price: float
    duration_months: int
    features: List[str] = []

class SubscriptionPlanResponse(BaseModel):
    id: str
    name: str
    max_employees: int
    price: float
    duration_months: int
    features: List[str]
    is_active: bool = True

class TaskCreate(BaseModel):
    title: str
    description: str
    priority: str
    assigned_to: str
    due_date: str
    requires_proof: bool = False

class TaskUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    priority: Optional[str] = None
    assigned_to: Optional[str] = None
    due_date: Optional[str] = None
    status: Optional[str] = None
    requires_proof: Optional[bool] = None

class TaskResponse(BaseModel):
    id: str
    company_id: str
    assigned_to: str
    assigned_to_name: Optional[str] = None
    title: str
    description: str
    priority: str
    status: str
    due_date: str
    requires_proof: bool
    proof_files: List[str] = []
    created_by: str
    created_at: str
    completed_at: Optional[str] = None

class AttendanceCheckIn(BaseModel):
    qr_code: str
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class AttendanceCheckOut(BaseModel):
    latitude: Optional[float] = None
    longitude: Optional[float] = None

class AttendanceResponse(BaseModel):
    id: str
    employee_id: str
    employee_name: Optional[str] = None
    company_id: str
    date: str
    check_in_time: Optional[str] = None
    check_out_time: Optional[str] = None
    check_in_location: Optional[Dict[str, float]] = None
    check_out_location: Optional[Dict[str, float]] = None
    status: str

class ReportCreate(BaseModel):
    title: str
    description: str
    files: List[str] = []
    images: List[str] = []

class ReportResponse(BaseModel):
    id: str
    employee_id: str
    employee_name: Optional[str] = None
    company_id: str
    title: str
    description: str
    files: List[str]
    images: List[str]
    status: str
    created_at: str

class NotificationResponse(BaseModel):
    id: str
    user_id: str
    company_id: Optional[str] = None
    type: str
    title: str
    message: str
    read_status: bool
    created_at: str

class DepartmentCreate(BaseModel):
    name: str
    head_id: Optional[str] = None

class DepartmentResponse(BaseModel):
    id: str
    company_id: str
    name: str
    head_id: Optional[str] = None
    head_name: Optional[str] = None
    created_at: str

# ============ Helper Functions ============

def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.now(timezone.utc) + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt

def generate_qr_code(data: str) -> str:
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    buffered = BytesIO()
    img.save(buffered, format="PNG")
    img_str = base64.b64encode(buffered.getvalue()).decode()
    return f"data:image/png;base64,{img_str}"

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security)):
    try:
        token = credentials.credentials
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise HTTPException(status_code=401, detail="Invalid token")
        
        user = await db.users.find_one({"id": user_id}, {"_id": 0})
        if user is None:
            raise HTTPException(status_code=401, detail="User not found")
        return user
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

# ============ Auth Routes ============

@api_router.post("/auth/login", response_model=LoginResponse)
async def login(request: LoginRequest):
    # Find user by email or phone
    user = await db.users.find_one(
        {"$or": [{"email": request.email_or_phone}, {"phone": request.email_or_phone}]},
        {"_id": 0}
    )
    
    if not user or not verify_password(request.password, user["password"]):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    
    # Create token
    token = create_access_token({"sub": user["id"], "role": user["role"]})
    
    # Remove password from response
    user_data = {k: v for k, v in user.items() if k != "password"}
    
    return LoginResponse(token=token, user=user_data, role=user["role"])

@api_router.get("/auth/me", response_model=UserResponse)
async def get_me(current_user: dict = Depends(get_current_user)):
    return UserResponse(**{k: v for k, v in current_user.items() if k != "password"})

# ============ Super Admin Routes ============

@api_router.get("/admin/statistics")
async def get_admin_statistics(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    total_companies = await db.companies.count_documents({})
    active_companies = await db.companies.count_documents({"subscription_status": "active"})
    total_employees = await db.users.count_documents({"role": UserRole.EMPLOYEE})
    
    # Calculate total revenue (mock for now)
    total_revenue = active_companies * 100  # Simplified
    
    return {
        "total_companies": total_companies,
        "active_companies": active_companies,
        "inactive_companies": total_companies - active_companies,
        "total_employees": total_employees,
        "total_revenue": total_revenue
    }

@api_router.get("/admin/companies", response_model=List[CompanyResponse])
async def get_all_companies(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    companies = await db.companies.find({}, {"_id": 0}).to_list(1000)
    
    # Add employee count to each company
    for company in companies:
        count = await db.users.count_documents({"company_id": company["id"], "role": UserRole.EMPLOYEE})
        company["employee_count"] = count
    
    return companies

@api_router.post("/admin/companies", response_model=CompanyResponse)
async def create_company(company: CompanyCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if owner email already exists
    existing = await db.users.find_one({"email": company.owner_email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    company_id = str(uuid.uuid4())
    owner_id = str(uuid.uuid4())
    
    # Generate QR code for company
    qr_data = f"company:{company_id}"
    qr_code = generate_qr_code(qr_data)
    
    # Create owner user
    owner_user = {
        "id": owner_id,
        "email": company.owner_email,
        "phone": company.owner_phone,
        "password": hash_password(company.owner_password),
        "name": company.owner_name,
        "role": UserRole.COMPANY_OWNER,
        "company_id": company_id,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.users.insert_one(owner_user)
    
    # Create company
    company_doc = {
        "id": company_id,
        "name": company.name,
        "owner_id": owner_id,
        "qr_code": qr_code,
        "subscription_status": "active",
        "subscription_plan_id": company.subscription_plan_id,
        "subscription_end_date": None,
        "address": company.address,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "employee_count": 0
    }
    await db.companies.insert_one(company_doc)
    
    return CompanyResponse(**company_doc)

@api_router.put("/admin/companies/{company_id}")
async def update_company(company_id: str, updates: dict, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    result = await db.companies.update_one({"id": company_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Company not found")
    
    return {"message": "Company updated successfully"}

@api_router.delete("/admin/companies/{company_id}")
async def delete_company(company_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Delete company and all related data
    await db.companies.delete_one({"id": company_id})
    await db.users.delete_many({"company_id": company_id})
    await db.tasks.delete_many({"company_id": company_id})
    await db.attendance.delete_many({"company_id": company_id})
    await db.reports.delete_many({"company_id": company_id})
    
    return {"message": "Company deleted successfully"}

@api_router.get("/admin/subscription-plans", response_model=List[SubscriptionPlanResponse])
async def get_subscription_plans(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    plans = await db.subscription_plans.find({}, {"_id": 0}).to_list(100)
    return plans

@api_router.post("/admin/subscription-plans", response_model=SubscriptionPlanResponse)
async def create_subscription_plan(plan: SubscriptionPlanCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    plan_doc = {
        "id": str(uuid.uuid4()),
        **plan.model_dump(),
        "is_active": True
    }
    await db.subscription_plans.insert_one(plan_doc)
    return SubscriptionPlanResponse(**plan_doc)

@api_router.put("/admin/subscription-plans/{plan_id}")
async def update_subscription_plan(plan_id: str, updates: dict, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.SUPER_ADMIN:
        raise HTTPException(status_code=403, detail="Access denied")
    
    result = await db.subscription_plans.update_one({"id": plan_id}, {"$set": updates})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Plan not found")
    
    return {"message": "Plan updated successfully"}

# ============ Company Owner Routes ============

@api_router.get("/owner/dashboard")
async def get_owner_dashboard(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    company_id = current_user["company_id"]
    today = datetime.now(timezone.utc).date().isoformat()
    
    # Get statistics
    total_employees = await db.users.count_documents({"company_id": company_id, "role": UserRole.EMPLOYEE})
    present_today = await db.attendance.count_documents({
        "company_id": company_id,
        "date": today,
        "status": AttendanceStatus.PRESENT
    })
    late_today = await db.attendance.count_documents({
        "company_id": company_id,
        "date": today,
        "status": AttendanceStatus.LATE
    })
    absent_today = total_employees - present_today - late_today
    
    open_tasks = await db.tasks.count_documents({
        "company_id": company_id,
        "status": {"$in": [TaskStatus.NEW, TaskStatus.IN_PROGRESS]}
    })
    completed_tasks = await db.tasks.count_documents({
        "company_id": company_id,
        "status": TaskStatus.COMPLETED
    })
    overdue_tasks = await db.tasks.count_documents({
        "company_id": company_id,
        "status": TaskStatus.OVERDUE
    })
    
    # Get recent reports
    recent_reports = await db.reports.find(
        {"company_id": company_id},
        {"_id": 0}
    ).sort("created_at", -1).limit(5).to_list(5)
    
    return {
        "total_employees": total_employees,
        "present_today": present_today,
        "late_today": late_today,
        "absent_today": absent_today,
        "open_tasks": open_tasks,
        "completed_tasks": completed_tasks,
        "overdue_tasks": overdue_tasks,
        "recent_reports": recent_reports
    }

@api_router.get("/owner/employees", response_model=List[UserResponse])
async def get_employees(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    employees = await db.users.find(
        {"company_id": current_user["company_id"], "role": UserRole.EMPLOYEE},
        {"_id": 0, "password": 0}
    ).to_list(1000)
    
    return employees

@api_router.post("/owner/employees", response_model=UserResponse)
async def create_employee(employee: UserCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Check if email already exists
    existing = await db.users.find_one({"email": employee.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")
    
    employee_doc = {
        "id": str(uuid.uuid4()),
        "email": employee.email,
        "phone": employee.phone,
        "password": hash_password(employee.password),
        "name": employee.name,
        "role": UserRole.EMPLOYEE,
        "company_id": current_user["company_id"],
        "department": employee.department,
        "position": employee.position,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.users.insert_one(employee_doc)
    
    return UserResponse(**{k: v for k, v in employee_doc.items() if k != "password"})

@api_router.put("/owner/employees/{employee_id}")
async def update_employee(employee_id: str, updates: dict, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Ensure can only update employees from same company
    result = await db.users.update_one(
        {"id": employee_id, "company_id": current_user["company_id"], "role": UserRole.EMPLOYEE},
        {"$set": updates}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    return {"message": "Employee updated successfully"}

@api_router.delete("/owner/employees/{employee_id}")
async def delete_employee(employee_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    result = await db.users.delete_one(
        {"id": employee_id, "company_id": current_user["company_id"], "role": UserRole.EMPLOYEE}
    )
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Employee not found")
    
    return {"message": "Employee deleted successfully"}

@api_router.get("/owner/tasks", response_model=List[TaskResponse])
async def get_tasks(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    tasks = await db.tasks.find(
        {"company_id": current_user["company_id"]},
        {"_id": 0}
    ).to_list(1000)
    
    # Add employee names
    for task in tasks:
        employee = await db.users.find_one({"id": task["assigned_to"]}, {"_id": 0})
        if employee:
            task["assigned_to_name"] = employee["name"]
    
    return tasks

@api_router.post("/owner/tasks", response_model=TaskResponse)
async def create_task(task: TaskCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    task_doc = {
        "id": str(uuid.uuid4()),
        "company_id": current_user["company_id"],
        "assigned_to": task.assigned_to,
        "title": task.title,
        "description": task.description,
        "priority": task.priority,
        "status": TaskStatus.NEW,
        "due_date": task.due_date,
        "requires_proof": task.requires_proof,
        "proof_files": [],
        "created_by": current_user["id"],
        "created_at": datetime.now(timezone.utc).isoformat(),
        "completed_at": None
    }
    await db.tasks.insert_one(task_doc)
    
    # Create notification for employee
    notification = {
        "id": str(uuid.uuid4()),
        "user_id": task.assigned_to,
        "company_id": current_user["company_id"],
        "type": "task_assigned",
        "title": "مهمة جديدة",
        "message": f"تم تعيين مهمة جديدة لك: {task.title}",
        "read_status": False,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.notifications.insert_one(notification)
    
    # Get employee name
    employee = await db.users.find_one({"id": task.assigned_to}, {"_id": 0})
    if employee:
        task_doc["assigned_to_name"] = employee["name"]
    
    return TaskResponse(**task_doc)

@api_router.put("/owner/tasks/{task_id}")
async def update_task(task_id: str, updates: TaskUpdate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    update_dict = {k: v for k, v in updates.model_dump().items() if v is not None}
    
    result = await db.tasks.update_one(
        {"id": task_id, "company_id": current_user["company_id"]},
        {"$set": update_dict}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"message": "Task updated successfully"}

@api_router.delete("/owner/tasks/{task_id}")
async def delete_task(task_id: str, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    result = await db.tasks.delete_one(
        {"id": task_id, "company_id": current_user["company_id"]}
    )
    
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"message": "Task deleted successfully"}

@api_router.get("/owner/attendance", response_model=List[AttendanceResponse])
async def get_attendance(current_user: dict = Depends(get_current_user), date: Optional[str] = None):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    query = {"company_id": current_user["company_id"]}
    if date:
        query["date"] = date
    else:
        query["date"] = datetime.now(timezone.utc).date().isoformat()
    
    attendance = await db.attendance.find(query, {"_id": 0}).to_list(1000)
    
    # Add employee names
    for record in attendance:
        employee = await db.users.find_one({"id": record["employee_id"]}, {"_id": 0})
        if employee:
            record["employee_name"] = employee["name"]
    
    return attendance

@api_router.get("/owner/reports", response_model=List[ReportResponse])
async def get_reports(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    reports = await db.reports.find(
        {"company_id": current_user["company_id"]},
        {"_id": 0}
    ).sort("created_at", -1).to_list(1000)
    
    # Add employee names
    for report in reports:
        employee = await db.users.find_one({"id": report["employee_id"]}, {"_id": 0})
        if employee:
            report["employee_name"] = employee["name"]
    
    return reports

@api_router.get("/owner/departments", response_model=List[DepartmentResponse])
async def get_departments(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    departments = await db.departments.find(
        {"company_id": current_user["company_id"]},
        {"_id": 0}
    ).to_list(100)
    
    # Add head names
    for dept in departments:
        if dept.get("head_id"):
            head = await db.users.find_one({"id": dept["head_id"]}, {"_id": 0})
            if head:
                dept["head_name"] = head["name"]
    
    return departments

@api_router.post("/owner/departments", response_model=DepartmentResponse)
async def create_department(department: DepartmentCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.COMPANY_OWNER:
        raise HTTPException(status_code=403, detail="Access denied")
    
    dept_doc = {
        "id": str(uuid.uuid4()),
        "company_id": current_user["company_id"],
        "name": department.name,
        "head_id": department.head_id,
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.departments.insert_one(dept_doc)
    
    # Add head name if exists
    if dept_doc.get("head_id"):
        head = await db.users.find_one({"id": dept_doc["head_id"]}, {"_id": 0})
        if head:
            dept_doc["head_name"] = head["name"]
    
    return DepartmentResponse(**dept_doc)

# ============ Employee Routes ============

@api_router.get("/employee/dashboard")
async def get_employee_dashboard(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    employee_id = current_user["id"]
    today = datetime.now(timezone.utc).date().isoformat()
    
    # Get statistics
    total_tasks = await db.tasks.count_documents({"assigned_to": employee_id})
    completed_tasks = await db.tasks.count_documents({
        "assigned_to": employee_id,
        "status": TaskStatus.COMPLETED
    })
    pending_tasks = await db.tasks.count_documents({
        "assigned_to": employee_id,
        "status": {"$in": [TaskStatus.NEW, TaskStatus.IN_PROGRESS]}
    })
    
    # Calculate completion rate
    completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
    
    # Get today's attendance
    attendance = await db.attendance.find_one(
        {"employee_id": employee_id, "date": today},
        {"_id": 0}
    )
    
    # Get latest notification
    latest_notification = await db.notifications.find_one(
        {"user_id": employee_id},
        {"_id": 0},
        sort=[("created_at", -1)]
    )
    
    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "pending_tasks": pending_tasks,
        "completion_rate": round(completion_rate, 1),
        "checked_in": attendance is not None and attendance.get("check_in_time") is not None,
        "checked_out": attendance is not None and attendance.get("check_out_time") is not None,
        "latest_notification": latest_notification
    }

@api_router.get("/employee/tasks", response_model=List[TaskResponse])
async def get_employee_tasks(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    tasks = await db.tasks.find(
        {"assigned_to": current_user["id"]},
        {"_id": 0}
    ).to_list(1000)
    
    for task in tasks:
        task["assigned_to_name"] = current_user["name"]
    
    return tasks

@api_router.put("/employee/tasks/{task_id}/status")
async def update_task_status(task_id: str, status_data: dict, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    update_data = {"status": status_data.get("status")}
    if status_data.get("status") == TaskStatus.COMPLETED:
        update_data["completed_at"] = datetime.now(timezone.utc).isoformat()
    
    result = await db.tasks.update_one(
        {"id": task_id, "assigned_to": current_user["id"]},
        {"$set": update_data}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"message": "Task status updated successfully"}

@api_router.post("/employee/tasks/{task_id}/proof")
async def upload_task_proof(task_id: str, proof: dict, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    result = await db.tasks.update_one(
        {"id": task_id, "assigned_to": current_user["id"]},
        {"$push": {"proof_files": proof.get("file_url")}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Task not found")
    
    return {"message": "Proof uploaded successfully"}

@api_router.post("/employee/attendance/check-in")
async def check_in(data: AttendanceCheckIn, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    # Verify QR code
    company = await db.companies.find_one({"id": current_user["company_id"]}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    expected_qr = f"company:{current_user['company_id']}"
    if data.qr_code != expected_qr:
        raise HTTPException(status_code=400, detail="Invalid QR code")
    
    today = datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc)
    
    # Check if already checked in
    existing = await db.attendance.find_one({
        "employee_id": current_user["id"],
        "date": today
    })
    
    if existing and existing.get("check_in_time"):
        raise HTTPException(status_code=400, detail="Already checked in today")
    
    # Determine status (late if after 9 AM)
    hour = now.hour
    attendance_status = AttendanceStatus.LATE if hour >= 9 else AttendanceStatus.PRESENT
    
    location = None
    if data.latitude and data.longitude:
        location = {"latitude": data.latitude, "longitude": data.longitude}
    
    attendance_doc = {
        "id": str(uuid.uuid4()),
        "employee_id": current_user["id"],
        "company_id": current_user["company_id"],
        "date": today,
        "check_in_time": now.isoformat(),
        "check_out_time": None,
        "check_in_location": location,
        "check_out_location": None,
        "status": attendance_status
    }
    
    if existing:
        await db.attendance.update_one(
            {"id": existing["id"]},
            {"$set": attendance_doc}
        )
    else:
        await db.attendance.insert_one(attendance_doc)
    
    return {"message": "Checked in successfully", "status": attendance_status}

@api_router.post("/employee/attendance/check-out")
async def check_out(data: AttendanceCheckOut, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    today = datetime.now(timezone.utc).date().isoformat()
    now = datetime.now(timezone.utc)
    
    # Check if checked in
    attendance = await db.attendance.find_one({
        "employee_id": current_user["id"],
        "date": today
    })
    
    if not attendance or not attendance.get("check_in_time"):
        raise HTTPException(status_code=400, detail="Not checked in yet")
    
    if attendance.get("check_out_time"):
        raise HTTPException(status_code=400, detail="Already checked out")
    
    location = None
    if data.latitude and data.longitude:
        location = {"latitude": data.latitude, "longitude": data.longitude}
    
    await db.attendance.update_one(
        {"id": attendance["id"]},
        {"$set": {
            "check_out_time": now.isoformat(),
            "check_out_location": location
        }}
    )
    
    return {"message": "Checked out successfully"}

@api_router.get("/employee/attendance/history", response_model=List[AttendanceResponse])
async def get_attendance_history(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    attendance = await db.attendance.find(
        {"employee_id": current_user["id"]},
        {"_id": 0}
    ).sort("date", -1).limit(30).to_list(30)
    
    for record in attendance:
        record["employee_name"] = current_user["name"]
    
    return attendance

@api_router.get("/employee/performance")
async def get_employee_performance(current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    employee_id = current_user["id"]
    
    # Get task statistics
    total_tasks = await db.tasks.count_documents({"assigned_to": employee_id})
    completed_tasks = await db.tasks.count_documents({
        "assigned_to": employee_id,
        "status": TaskStatus.COMPLETED
    })
    overdue_tasks = await db.tasks.count_documents({
        "assigned_to": employee_id,
        "status": TaskStatus.OVERDUE
    })
    
    # Get attendance statistics
    total_days = await db.attendance.count_documents({"employee_id": employee_id})
    present_days = await db.attendance.count_documents({
        "employee_id": employee_id,
        "status": AttendanceStatus.PRESENT
    })
    late_days = await db.attendance.count_documents({
        "employee_id": employee_id,
        "status": AttendanceStatus.LATE
    })
    absent_days = await db.attendance.count_documents({
        "employee_id": employee_id,
        "status": AttendanceStatus.ABSENT
    })
    
    # Calculate metrics
    completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
    attendance_rate = (present_days / total_days * 100) if total_days > 0 else 0
    
    # Simple performance rating
    performance_score = (completion_rate * 0.6 + attendance_rate * 0.4)
    
    return {
        "total_tasks": total_tasks,
        "completed_tasks": completed_tasks,
        "overdue_tasks": overdue_tasks,
        "completion_rate": round(completion_rate, 1),
        "total_days": total_days,
        "present_days": present_days,
        "late_days": late_days,
        "absent_days": absent_days,
        "attendance_rate": round(attendance_rate, 1),
        "performance_score": round(performance_score, 1)
    }

@api_router.post("/employee/reports", response_model=ReportResponse)
async def create_report(report: ReportCreate, current_user: dict = Depends(get_current_user)):
    if current_user["role"] != UserRole.EMPLOYEE:
        raise HTTPException(status_code=403, detail="Access denied")
    
    report_doc = {
        "id": str(uuid.uuid4()),
        "employee_id": current_user["id"],
        "employee_name": current_user["name"],
        "company_id": current_user["company_id"],
        "title": report.title,
        "description": report.description,
        "files": report.files,
        "images": report.images,
        "status": "pending",
        "created_at": datetime.now(timezone.utc).isoformat()
    }
    await db.reports.insert_one(report_doc)
    
    return ReportResponse(**report_doc)

@api_router.put("/employee/profile")
async def update_profile(updates: dict, current_user: dict = Depends(get_current_user)):
    # Only allow updating certain fields
    allowed_fields = ["name", "phone", "avatar"]
    filtered_updates = {k: v for k, v in updates.items() if k in allowed_fields}
    
    if not filtered_updates:
        raise HTTPException(status_code=400, detail="No valid fields to update")
    
    result = await db.users.update_one(
        {"id": current_user["id"]},
        {"$set": filtered_updates}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="User not found")
    
    return {"message": "Profile updated successfully"}

# ============ Common Routes ============

@api_router.get("/notifications", response_model=List[NotificationResponse])
async def get_notifications(current_user: dict = Depends(get_current_user)):
    notifications = await db.notifications.find(
        {"user_id": current_user["id"]},
        {"_id": 0}
    ).sort("created_at", -1).limit(50).to_list(50)
    
    return notifications

@api_router.put("/notifications/{notification_id}/read")
async def mark_notification_read(notification_id: str, current_user: dict = Depends(get_current_user)):
    result = await db.notifications.update_one(
        {"id": notification_id, "user_id": current_user["id"]},
        {"$set": {"read_status": True}}
    )
    
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Notification not found")
    
    return {"message": "Notification marked as read"}

@api_router.get("/company/{company_id}/qr")
async def get_company_qr(company_id: str):
    company = await db.companies.find_one({"id": company_id}, {"_id": 0})
    if not company:
        raise HTTPException(status_code=404, detail="Company not found")
    
    return {"qr_code": company["qr_code"]}

# ============ Seed Data (For Testing) ============

@api_router.post("/seed")
async def seed_data():
    # Create Super Admin
    admin_id = "admin-001"
    admin = await db.users.find_one({"id": admin_id})
    if not admin:
        admin_doc = {
            "id": admin_id,
            "email": "admin@jaz.com",
            "phone": "0500000000",
            "password": hash_password("admin123"),
            "name": "Super Admin",
            "role": UserRole.SUPER_ADMIN,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.users.insert_one(admin_doc)
    
    # Create a sample subscription plan
    plan_id = "plan-001"
    plan = await db.subscription_plans.find_one({"id": plan_id})
    if not plan:
        plan_doc = {
            "id": plan_id,
            "name": "خطة صغيرة",
            "max_employees": 10,
            "price": 99.0,
            "duration_months": 1,
            "features": ["إدارة الموظفين", "نظام الحضور", "إدارة المهام"],
            "is_active": True
        }
        await db.subscription_plans.insert_one(plan_doc)
    
    # Create a demo company with owner and employees
    company_id = "company-001"
    company = await db.companies.find_one({"id": company_id})
    if not company:
        owner_id = "owner-001"
        qr_data = f"company:{company_id}"
        qr_code = generate_qr_code(qr_data)
        
        owner_doc = {
            "id": owner_id,
            "email": "owner@demo.com",
            "phone": "0501111111",
            "password": hash_password("owner123"),
            "name": "أحمد محمد",
            "role": UserRole.COMPANY_OWNER,
            "company_id": company_id,
            "status": "active",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.users.insert_one(owner_doc)
        
        company_doc = {
            "id": company_id,
            "name": "شركة النجاح",
            "owner_id": owner_id,
            "qr_code": qr_code,
            "subscription_status": "active",
            "subscription_plan_id": plan_id,
            "subscription_end_date": (datetime.now(timezone.utc) + timedelta(days=30)).isoformat(),
            "address": "الرياض، المملكة العربية السعودية",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.companies.insert_one(company_doc)
        
        # Create demo employees
        employees = [
            {
                "id": "emp-001",
                "email": "employee1@demo.com",
                "phone": "0502222222",
                "password": hash_password("emp123"),
                "name": "فاطمة أحمد",
                "role": UserRole.EMPLOYEE,
                "company_id": company_id,
                "department": "المبيعات",
                "position": "مندوب مبيعات",
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat()
            },
            {
                "id": "emp-002",
                "email": "employee2@demo.com",
                "phone": "0503333333",
                "password": hash_password("emp123"),
                "name": "محمد خالد",
                "role": UserRole.EMPLOYEE,
                "company_id": company_id,
                "department": "التسويق",
                "position": "مسوق رقمي",
                "status": "active",
                "created_at": datetime.now(timezone.utc).isoformat()
            }
        ]
        await db.users.insert_many(employees)
        
        # Create demo tasks
        tasks = [
            {
                "id": "task-001",
                "company_id": company_id,
                "assigned_to": "emp-001",
                "title": "متابعة العملاء الجدد",
                "description": "التواصل مع قائمة العملاء المحتملين وإرسال العروض",
                "priority": TaskPriority.HIGH,
                "status": TaskStatus.NEW,
                "due_date": (datetime.now(timezone.utc) + timedelta(days=2)).date().isoformat(),
                "requires_proof": False,
                "proof_files": [],
                "created_by": owner_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None
            },
            {
                "id": "task-002",
                "company_id": company_id,
                "assigned_to": "emp-002",
                "title": "تصميم حملة إعلانية",
                "description": "إنشاء محتوى إبداعي للحملة الإعلانية القادمة",
                "priority": TaskPriority.MEDIUM,
                "status": TaskStatus.IN_PROGRESS,
                "due_date": (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat(),
                "requires_proof": True,
                "proof_files": [],
                "created_by": owner_id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "completed_at": None
            }
        ]
        await db.tasks.insert_many(tasks)
        
        # Create demo department
        dept_doc = {
            "id": "dept-001",
            "company_id": company_id,
            "name": "المبيعات",
            "head_id": "emp-001",
            "created_at": datetime.now(timezone.utc).isoformat()
        }
        await db.departments.insert_one(dept_doc)
    
    return {"message": "Seed data created successfully", "admin_email": "admin@jaz.com", "admin_password": "admin123", "owner_email": "owner@demo.com", "owner_password": "owner123", "employee_email": "employee1@demo.com", "employee_password": "emp123"}

# Include router
app.include_router(api_router)

app.add_middleware(
    CORSMiddleware,
    allow_credentials=True,
    allow_origins=os.environ.get('CORS_ORIGINS', '*').split(','),
    allow_methods=["*"],
    allow_headers=["*"],
)

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

@app.on_event("shutdown")
async def shutdown_db_client():
    client.close()