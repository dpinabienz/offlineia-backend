from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import supabase
import os
from datetime import datetime, timedelta
import uuid
import hashlib

# ─────────────────────────────────────────
# CONFIGURACION
# ─────────────────────────────────────────

SUPABASE_URL = os.getenv("SUPABASE_URL", "https://tboqopwtslhrjdndwhcm.supabase.co")
SUPABASE_KEY = os.getenv("SUPABASE_KEY", "sb_secret_1TT3N81jalkCL4hQinmR-Q_S62Snc3W")

client = supabase.create_client(SUPABASE_URL, SUPABASE_KEY)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────
# MODELOS
# ─────────────────────────────────────────

class ValidarLicenciaRequest(BaseModel):
    codigo: str
    hardware_id: str
    nombre_dispositivo: str = None
    sistema_operativo: str = None

class RegistrarInstalacionRequest(BaseModel):
    codigo: str
    hardware_id: str
    nombre_dispositivo: str = None
    sistema_operativo: str = None
    ip_address: str = None

class RegistrarTelemetriaRequest(BaseModel):
    codigo: str
    tipo_tarea: str  # 'resumen', 'tabla', 'presentacion', 'chat'
    cantidad: int = 1

class CrearLicenciaRequest(BaseModel):
    email: str

# ─────────────────────────────────────────
# ENDPOINTS - LICENCIAS
# ─────────────────────────────────────────

@app.post("/api/licencias/validar")
def validar_licencia(req: ValidarLicenciaRequest):
    """
    Valida si una licencia existe y está activa.
    Registra la instalación si es la primera vez.
    """
    try:
        # Buscar licencia
        resultado = client.table("licencias").select("*").eq("codigo", req.codigo).execute()
        
        if not resultado.data:
            return {"valida": False, "error": "Licencia no encontrada"}
        
        licencia = resultado.data[0]
        
        # Verificar si está activa
        if not licencia["activa"]:
            return {
                "valida": False,
                "error": "Licencia revocada",
                "email_contacto": licencia["email"],
                "motivo": licencia.get("notas", "")
            }
        
        license_id = licencia["id"]
        
        # Verificar si esta es una nueva instalación
        instalacion_existente = client.table("instalaciones").select("*").eq(
            "hardware_id", req.hardware_id
        ).eq("license_id", license_id).execute()
        
        if not instalacion_existente.data:
            # Nueva instalación - registrarla
            client.table("instalaciones").insert({
                "license_id": license_id,
                "hardware_id": req.hardware_id,
                "nombre_dispositivo": req.nombre_dispositivo,
                "sistema_operativo": req.sistema_operativo,
            }).execute()
        else:
            # Actualizar último acceso
            client.table("instalaciones").update({
                "ultimo_acceso": datetime.utcnow().isoformat()
            }).eq("hardware_id", req.hardware_id).execute()
        
        return {
            "valida": True,
            "email": licencia["email"],
            "license_id": license_id
        }
    
    except Exception as e:
        return {"valida": False, "error": str(e)}

@app.post("/api/licencias/crear")
def crear_licencia(req: CrearLicenciaRequest):
    """
    Crea una nueva licencia (solo para ti, Diego).
    En producción, esto estaría protegido con autenticación.
    """
    try:
        codigo = f"OFF-{uuid.uuid4().hex[:16].upper()}"
        
        resultado = client.table("licencias").insert({
            "codigo": codigo,
            "email": req.email,
            "activa": True,
        }).execute()
        
        return {
            "exito": True,
            "codigo": codigo,
            "email": req.email
        }
    
    except Exception as e:
        return {"exito": False, "error": str(e)}

@app.post("/api/licencias/revocar")
def revocar_licencia(codigo: str):
    """
    Revoca una licencia (protegido en producción con autenticación).
    """
    try:
        client.table("licencias").update({
            "activa": False,
            "fecha_revocacion": datetime.utcnow().isoformat(),
            "notas": "Revocada por administrador"
        }).eq("codigo", codigo).execute()
        
        return {"exito": True, "mensaje": f"Licencia {codigo} revocada"}
    
    except Exception as e:
        return {"exito": False, "error": str(e)}

@app.get("/api/licencias/{codigo}")
def obtener_licencia(codigo: str):
    """
    Obtiene info de una licencia y sus instalaciones.
    """
    try:
        resultado = client.table("licencias").select("*").eq("codigo", codigo).execute()
        
        if not resultado.data:
            raise HTTPException(status_code=404, detail="Licencia no encontrada")
        
        licencia = resultado.data[0]
        
        # Obtener instalaciones asociadas
        instalaciones = client.table("instalaciones").select("*").eq(
            "license_id", licencia["id"]
        ).execute()
        
        # Obtener telemetría
        telemetria = client.table("telemetria").select("*").eq(
            "license_id", licencia["id"]
        ).execute()
        
        # Agrupar telemetría por tipo
        telem_por_tipo = {}
        for item in telemetria.data:
            tipo = item["tipo_tarea"]
            if tipo not in telem_por_tipo:
                telem_por_tipo[tipo] = 0
            telem_por_tipo[tipo] += item["cantidad"]
        
        return {
            "codigo": licencia["codigo"],
            "email": licencia["email"],
            "activa": licencia["activa"],
            "fecha_creacion": licencia["fecha_creacion"],
            "fecha_revocacion": licencia["fecha_revocacion"],
            "instalaciones": len(instalaciones.data),
            "detalles_instalaciones": instalaciones.data,
            "telemetria_total": telem_por_tipo,
            "total_eventos": sum(telem_por_tipo.values())
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────
# ENDPOINTS - TELEMETRIA
# ─────────────────────────────────────────

@app.post("/api/telemetria/registrar")
def registrar_telemetria(req: RegistrarTelemetriaRequest):
    """
    Registra un evento de telemetría.
    Se llama cada vez que el usuario hace una tarea.
    """
    try:
        # Verificar que la licencia existe y está activa
        resultado = client.table("licencias").select("id").eq(
            "codigo", req.codigo
        ).eq("activa", True).execute()
        
        if not resultado.data:
            return {"exito": False, "error": "Licencia no válida"}
        
        license_id = resultado.data[0]["id"]
        
        # Registrar evento
        client.table("telemetria").insert({
            "license_id": license_id,
            "tipo_tarea": req.tipo_tarea,
            "cantidad": req.cantidad,
        }).execute()
        
        return {"exito": True}
    
    except Exception as e:
        return {"exito": False, "error": str(e)}

@app.get("/api/telemetria/{codigo}")
def obtener_telemetria(codigo: str):
    """
    Obtiene la telemetría acumulada de una licencia.
    """
    try:
        resultado = client.table("licencias").select("id").eq("codigo", codigo).execute()
        
        if not resultado.data:
            raise HTTPException(status_code=404, detail="Licencia no encontrada")
        
        license_id = resultado.data[0]["id"]
        
        telemetria = client.table("telemetria").select("*").eq(
            "license_id", license_id
        ).execute()
        
        # Agrupar por tipo
        telem_por_tipo = {}
        for item in telemetria.data:
            tipo = item["tipo_tarea"]
            if tipo not in telem_por_tipo:
                telem_por_tipo[tipo] = 0
            telem_por_tipo[tipo] += item["cantidad"]
        
        return {
            "codigo": codigo,
            "telemetria": telem_por_tipo,
            "total_eventos": sum(telem_por_tipo.values()),
            "eventos_brutos": telemetria.data
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────
# ENDPOINTS - VERSIONES
# ─────────────────────────────────────────

@app.post("/api/versiones/crear")
def crear_version(numero_version: str, url_descarga: str, obligatoria: bool = False):
    """
    Crea una nueva versión (solo para ti, Diego).
    """
    try:
        resultado = client.table("versiones").insert({
            "numero_version": numero_version,
            "url_descarga": url_descarga,
            "obligatoria": obligatoria,
        }).execute()
        
        return {
            "exito": True,
            "version": numero_version,
            "url": url_descarga
        }
    
    except Exception as e:
        return {"exito": False, "error": str(e)}

@app.get("/api/versiones/ultimas")
def obtener_ultimas_versiones(limite: int = 5):
    """
    Obtiene las últimas versiones disponibles.
    La app chequea esto 1 vez al mes para updates.
    """
    try:
        resultado = client.table("versiones").select("*").order(
            "fecha_lanzamiento", desc=True
        ).limit(limite).execute()
        
        return {
            "versiones": resultado.data
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/versiones/ultima")
def obtener_ultima_version():
    """
    Obtiene la última versión disponible.
    Esto es lo que llama la app para saber si hay updates.
    """
    try:
        resultado = client.table("versiones").select("*").order(
            "fecha_lanzamiento", desc=True
        ).limit(1).execute()
        
        if not resultado.data:
            return {"version": None, "url": None}
        
        v = resultado.data[0]
        return {
            "numero_version": v["numero_version"],
            "url_descarga": v["url_descarga"],
            "obligatoria": v["obligatoria"],
            "fecha_lanzamiento": v["fecha_lanzamiento"],
            "notas": v.get("notas_version", "")
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────
# ENDPOINTS - DASHBOARD (para ti)
# ─────────────────────────────────────────

@app.get("/api/dashboard/resumen")
def dashboard_resumen():
    """
    Resumen general de todas las licencias, instalaciones y uso.
    """
    try:
        licencias = client.table("licencias").select("*").execute()
        instalaciones = client.table("instalaciones").select("*").execute()
        telemetria = client.table("telemetria").select("*").execute()
        
        # Contar licencias activas
        activas = len([l for l in licencias.data if l["activa"]])
        revocadas = len([l for l in licencias.data if not l["activa"]])
        
        # Agrupar telemetría
        telem_por_tipo = {}
        for item in telemetria.data:
            tipo = item["tipo_tarea"]
            if tipo not in telem_por_tipo:
                telem_por_tipo[tipo] = 0
            telem_por_tipo[tipo] += item["cantidad"]
        
        # Último acceso más reciente
        ultimo_acceso = None
        if instalaciones.data:
            accesos = [i["ultimo_acceso"] for i in instalaciones.data if i["ultimo_acceso"]]
            if accesos:
                ultimo_acceso = max(accesos)
        
        return {
            "licencias_totales": len(licencias.data),
            "licencias_activas": activas,
            "licencias_revocadas": revocadas,
            "instalaciones_totales": len(instalaciones.data),
            "eventos_telemetria": telem_por_tipo,
            "total_eventos": sum(telem_por_tipo.values()),
            "ultimo_acceso": ultimo_acceso,
        }
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/dashboard/licencias")
def dashboard_licencias():
    """
    Lista todas las licencias con su info.
    """
    try:
        licencias = client.table("licencias").select("*").execute()
        
        resultado = []
        for lic in licencias.data:
            # Contar instalaciones
            inst = client.table("instalaciones").select("*").eq(
                "license_id", lic["id"]
            ).execute()
            
            # Contar telemetría
            telem = client.table("telemetria").select("*").eq(
                "license_id", lic["id"]
            ).execute()
            
            telem_por_tipo = {}
            for item in telem.data:
                tipo = item["tipo_tarea"]
                if tipo not in telem_por_tipo:
                    telem_por_tipo[tipo] = 0
                telem_por_tipo[tipo] += item["cantidad"]
            
            resultado.append({
                "codigo": lic["codigo"],
                "email": lic["email"],
                "activa": lic["activa"],
                "fecha_creacion": lic["fecha_creacion"],
                "instalaciones": len(inst.data),
                "eventos": sum(telem_por_tipo.values()),
                "tipos_uso": telem_por_tipo,
            })
        
        return {"licencias": resultado}
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ─────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────

@app.get("/health")
def health():
    return {"status": "ok"}

@app.get("/")
def root():
    return {"mensaje": "OfflineIA Backend - Licencias, Telemetría, Versiones"}
