from fastapi import FastAPI, HTTPException, Depends, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from pydantic import BaseModel
from sqlalchemy import create_engine, Column, String, Float, Integer, DateTime, Boolean, func
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker, Session
from datetime import datetime, timedelta
from typing import Optional, List
import os
import jwt
import requests
from passlib.context import CryptContext
from dotenv import load_dotenv

load_dotenv()

# Configuração
DATABASE_URL = os.getenv("DATABASE_URL")
SECRET_KEY = os.getenv("SECRET_KEY", "troque_isso_urgente")
ASAAS_API_KEY = os.getenv("ASAAS_API_KEY")

# FastAPI
app = FastAPI(title="PROMETHEUS CORE", version="2.0.0")
security = HTTPBearer()
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# Banco de dados
engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ============================================
# MODELOS
# ============================================

class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    pix_key = Column(String)
    pro_labore_percent = Column(Float, default=30)
    created_at = Column(DateTime, default=datetime.utcnow)

class Transaction(Base):
    __tablename__ = "transactions"
    id = Column(Integer, primary_key=True)
    amount = Column(Float)
    tipo = Column(String)
    status = Column(String, default="pending")
    description = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    executed_at = Column(DateTime, nullable=True)

class Decision(Base):
    __tablename__ = "decisions"
    id = Column(Integer, primary_key=True)
    title = Column(String)
    approved = Column(Boolean)
    score = Column(Integer)
    investment = Column(Float)
    expected_return = Column(Float)
    risk = Column(Integer)
    created_at = Column(DateTime, default=datetime.utcnow)

Base.metadata.create_all(bind=engine)

# ============================================
# FUNÇÕES DE AUTENTICAÇÃO
# ============================================

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()

def get_password_hash(password):
    return pwd_context.hash(password)

def verify_password(plain, hashed):
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict):
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(hours=24)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm="HS256")

async def get_current_user(credentials: HTTPAuthorizationCredentials = Depends(security), db: Session = Depends(get_db)):
    token = credentials.credentials
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=["HS256"])
        email = payload.get("sub")
        user = db.query(User).filter(User.email == email).first()
        if not user:
            raise HTTPException(status_code=401)
        return user
    except:
        raise HTTPException(status_code=401)

# ============================================
# ENDPOINTS
# ============================================

@app.get("/")
def root():
    return {"system": "PROMETHEUS CORE", "status": "ONLINE"}

@app.post("/auth/register")
def register(email: str, password: str, db: Session = Depends(get_db)):
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise HTTPException(status_code=400, detail="Email já existe")
    
    user = User(email=email, hashed_password=get_password_hash(password))
    db.add(user)
    db.commit()
    return {"message": "Usuário criado", "email": email}

@app.post("/auth/login")
def login(email: str, password: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == email).first()
    if not user or not verify_password(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Credenciais inválidas")
    
    token = create_access_token({"sub": user.email})
    return {"access_token": token, "token_type": "bearer"}

@app.post("/user/pix")
def register_pix(pix_key: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    current_user.pix_key = pix_key
    db.commit()
    return {"message": "Chave Pix cadastrada", "pix_key": pix_key}

@app.get("/finance/balance")
def get_balance(current_user: User = Depends(get_current_user)):
    if not ASAAS_API_KEY:
        raise HTTPException(status_code=500, detail="ASAAS_API_KEY não configurada")
    
    response = requests.get(
        "https://api.asaas.com/v3/finance/balance",
        headers={"access_token": ASAAS_API_KEY}
    )
    
    if response.status_code == 200:
        return response.json()
    raise HTTPException(status_code=response.status_code, detail="Erro ao consultar Asaas")

@app.post("/finance/prolabore/calculate")
def calculate_prolabore(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Lucro dos últimos 30 dias
    receita = db.query(func.sum(Transaction.amount)).filter(
        Transaction.tipo == "revenue",
        Transaction.status == "completed",
        Transaction.created_at >= datetime.utcnow() - timedelta(days=30)
    ).scalar() or 0
    
    despesa = db.query(func.sum(Transaction.amount)).filter(
        Transaction.tipo == "expense",
        Transaction.status == "completed",
        Transaction.created_at >= datetime.utcnow() - timedelta(days=30)
    ).scalar() or 0
    
    lucro = receita - despesa
    
    if lucro <= 0:
        return {"amount": 0, "profit": lucro, "message": "Sem lucro no período"}
    
    valor = lucro * (current_user.pro_labore_percent / 100)
    
    return {"amount": valor, "profit": lucro, "percent": current_user.pro_labore_percent}

@app.post("/finance/prolabore/transfer")
def transfer_prolabore(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    if not current_user.pix_key:
        raise HTTPException(status_code=400, detail="Cadastre sua chave Pix primeiro")
    
    if not ASAAS_API_KEY:
        raise HTTPException(status_code=500, detail="ASAAS_API_KEY não configurada")
    
    # Calcular valor
    receita = db.query(func.sum(Transaction.amount)).filter(
        Transaction.tipo == "revenue",
        Transaction.status == "completed",
        Transaction.created_at >= datetime.utcnow() - timedelta(days=30)
    ).scalar() or 0
    
    despesa = db.query(func.sum(Transaction.amount)).filter(
        Transaction.tipo == "expense",
        Transaction.status == "completed",
        Transaction.created_at >= datetime.utcnow() - timedelta(days=30)
    ).scalar() or 0
    
    lucro = receita - despesa
    
    if lucro <= 0:
        raise HTTPException(status_code=400, detail="Sem lucro no período")
    
    valor = lucro * (current_user.pro_labore_percent / 100)
    
    # Transferir via Asaas
    response = requests.post(
        "https://api.asaas.com/v3/transfers",
        headers={"access_token": ASAAS_API_KEY, "Content-Type": "application/json"},
        json={
            "value": valor,
            "pixAddressKey": current_user.pix_key,
            "description": f"Pró-labore {datetime.utcnow().strftime('%Y-%m')}"
        }
    )
    
    if response.status_code != 200:
        raise HTTPException(status_code=400, detail="Erro na transferência")
    
    # Registrar transação
    transacao = Transaction(
        amount=-valor,
        tipo="prolabore",
        status="completed",
        description=f"Pró-labore para {current_user.pix_key}",
        executed_at=datetime.utcnow()
    )
    db.add(transacao)
    db.commit()
    
    return {"message": "Transferência realizada", "amount": valor, "pix_key": current_user.pix_key}

@app.post("/decision/analyze")
def analyze_decision(title: str, investment: float, expected_return: float, risk: int, 
                     current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Regras de decisão
    approved = True
    reasons = []
    
    # Buscar caixa atual
    try:
        response = requests.get(
            "https://api.asaas.com/v3/finance/balance",
            headers={"access_token": ASAAS_API_KEY}
        )
        cash = response.json().get("balance", 0)
    except:
        cash = 0
    
    if investment > cash * 0.3:
        approved = False
        reasons.append(f"Investimento excede 30% do caixa (R$ {cash * 0.3:.2f})")
    
    if expected_return < 20:
        approved = False
        reasons.append(f"ROI mínimo é 20% (atual: {expected_return}%)")
    
    if risk > 70:
        approved = False
        reasons.append(f"Risco máximo é 70 (atual: {risk})")
    
    score = int((expected_return * 0.6) + (100 - risk) * 0.4)
    
    # Registrar decisão
    decision = Decision(
        title=title,
        approved=approved,
        score=score,
        investment=investment,
        expected_return=expected_return,
        risk=risk
    )
    db.add(decision)
    db.commit()
    
    return {
        "approved": approved,
        "score": score,
        "reasons": reasons if not approved else ["Todos os critérios atendidos"],
        "current_cash": cash
    }

@app.get("/dashboard")
def dashboard(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    # Saldo Asaas
    try:
        response = requests.get(
            "https://api.asaas.com/v3/finance/balance",
            headers={"access_token": ASAAS_API_KEY}
        )
        asaas_balance = response.json().get("balance", 0)
    except:
        asaas_balance = 0
    
    # Receita mês
    receita_mes = db.query(func.sum(Transaction.amount)).filter(
        Transaction.tipo == "revenue",
        Transaction.status == "completed",
        Transaction.created_at >= datetime.utcnow().replace(day=1)
    ).scalar() or 0
    
    # Despesa mês
    despesa_mes = db.query(func.sum(Transaction.amount)).filter(
        Transaction.tipo == "expense",
        Transaction.status == "completed",
        Transaction.created_at >= datetime.utcnow().replace(day=1)
    ).scalar() or 0
    
    # Últimas decisões
    ultimas_decisoes = db.query(Decision).order_by(Decision.created_at.desc()).limit(5).all()
    
    return {
        "asaas_balance": asaas_balance,
        "monthly_revenue": receita_mes,
        "monthly_expense": -despesa_mes if despesa_mes else 0,
        "monthly_profit": receita_mes - despesa_mes,
        "last_decisions": [
            {"title": d.title, "approved": d.approved, "score": d.score, "date": d.created_at}
            for d in ultimas_decisoes
        ]
    }
