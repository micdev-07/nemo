import os
import re
import json
import time
import shutil
from typing import Dict
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types

# --- CONFIGURATION INITIALE ---
app = FastAPI(title="NEMO Studio API", version="2.0.0")

# Support du CORS (Sans slash final pour éviter les Rejections CORS)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://nemostudio.netlify.app",
        "https://studio.netlify.app",
        "http://localhost:3000",
        "http://127.0.0.1:5500"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dossier local de stockage des projets sur Render
PROJECTS_DIR = "projects"
os.makedirs(PROJECTS_DIR, exist_ok=True)
app.mount("/projects", StaticFiles(directory=PROJECTS_DIR), name="projects")

# SDK Gemini et URL Backend
client = genai.Client()
BACKEND_URL = os.getenv("BACKEND_URL", "https://nemo-hdgw.onrender.com")

# --- PROMPT SYSTEME (SPECIALISTE FRONT-END) ---
SYSTEM_PROMPT_NEMO = """
Tu es NEMO, un développeur Front-end d'élite et UI/UX Designer d'exception.
Ton objectif est de concevoir des applications web Single Page (SPA), des portfolios, des landing pages et des outils web d'une qualité visuelle et fonctionnelle "Masterclass" (niveau Awwwards).

REGLES ET EXPERTISE TECHNIQUE :
1. PURE FRONT-END : Génère uniquement du HTML, CSS et JavaScript côté client. N'utilise AUCUN backend, ni API externe nécessitant des clés d'accès.
2. PERSISTANCE LOCALE (`localStorage`) : Si l'application nécessite de sauvegarder un état, utilise exclusivement le `localStorage`.
3. EXCELLENCE VISUELLE & UI/UX :
   - Design ultra moderne (gradients subtils, glassmorphism, animations fluides `@keyframes`, typographies Google Fonts, icônes SVG).
   - Layout 100% Responsive.
   - Code complet, propre, réactif, sans placeholders.
4. FORMAT DE RÉPONSE STRICT :
   Réponds EXCLUSIVEMENT sous la forme d'un objet JSON valide contenant la clé "files" avec tous les fichiers du projet (ex: index.html, style.css, script.js).
"""

# --- MODÈLES DE REQUÊTE ---
class CreateProjectRequest(BaseModel):
    prompt: str

class ModifyProjectRequest(BaseModel):
    project_id: str
    prompt: str
    current_files: Dict[str, str]

# --- FONCTIONS UTILITAIRES & PARSEUR BLINDÉ ---

def parse_llm_json(raw_text: str) -> dict:
    """
    Extrait et nettoie le JSON renvoyé par Gemini même s'il contient 
    des caractères d'échappement problématiques ou du markdown.
    """
    # 1. Nettoyage des balises Markdown (```json ... ```)
    cleaned = raw_text.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    # 2. Tentative de parse direct
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        pass

    # 3. Récupération du bloc JSON { ... } principal par Regex
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str, strict=False)
        except json.JSONDecodeError:
            # 4. Secours : correction des sauts de ligne non échappés dans les chaînes
            json_str_fixed = re.sub(r'(?<!\\)\n', r'\\n', json_str)
            return json.loads(json_str_fixed, strict=False)

    raise ValueError("Impossible de parser la structure JSON retournée par le modèle.")

def save_project_to_disk(project_id: str, files: dict) -> str:
    """Enregistre les fichiers du projet sur le disque dur du serveur Render."""
    clean_id = re.sub(r'[^\w\-]', '_', project_id).lower()
    output_dir = os.path.join(PROJECTS_DIR, clean_id)
    os.makedirs(output_dir, exist_ok=True)
    
    for file_path, content in files.items():
        clean_relative_path = os.path.normpath(file_path).lstrip("\\/")
        full_file_path = os.path.join(output_dir, clean_relative_path)
        os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
        
        with open(full_file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
    return clean_id

def remove_file(path: str):
    """Nettoyage en arrière-plan des fichiers ZIP générés."""
    try:
        if os.path.exists(path): 
            os.remove(path)
    except Exception as e:
        print(f"Erreur lors du nettoyage du ZIP : {e}")

# --- ROUTES API ---

@app.get("/")
async def root():
    return {"status": "online", "system": "NEMO Studio Engine v2.0"}

# 1. CRÉATION DE PROJET
@app.post("/api/create")
async def create_project(req: CreateProjectRequest):
    try:
        project_id = f"nemo_{int(time.time())}"
        prompt_create = f"{SYSTEM_PROMPT_NEMO}\n\nDEMANDE DE L'UTILISATEUR :\n{req.prompt}"
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt_create,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=8192
            )
        )
        
        # Extraction robuste du JSON
        project_data = parse_llm_json(response.text)
        files = project_data.get("files", project_data) if isinstance(project_data, dict) else {}
        
        if not files or not isinstance(files, dict):
            raise ValueError("Le dictionnaire des fichiers est invalide ou vide.")

        clean_id = save_project_to_disk(project_id, files)
        project_url = f"{BACKEND_URL}/projects/{clean_id}/index.html?t={int(time.time())}"
        
        return {
            "status": "success",
            "project_id": clean_id,
            "project_url": project_url,
            "files": files
        }
    except Exception as e:
        print(f"Erreur Création NEMO : {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 2. MODIFICATION DE PROJET
@app.post("/api/modify")
async def modify_project(req: ModifyProjectRequest):
    try:
        prompt_modif = f"{SYSTEM_PROMPT_NEMO}\n\nPROJET ACTUEL :\n{json.dumps(req.current_files)}\n\nMODIFICATION DEMANDÉE :\n{req.prompt}"
        
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt_modif,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3,
                max_output_tokens=8192
            )
        )
        
        # Extraction robuste du JSON
        project_data = parse_llm_json(response.text)
        files = project_data.get("files", project_data) if isinstance(project_data, dict) else {}
        
        if not files or not isinstance(files, dict):
            raise ValueError("Le dictionnaire des fichiers est invalide ou vide.")

        clean_id = save_project_to_disk(req.project_id, files)
        project_url = f"{BACKEND_URL}/projects/{clean_id}/index.html?t={int(time.time())}"
        
        return {
            "status": "success",
            "project_id": clean_id,
            "project_url": project_url,
            "files": files
        }
    except Exception as e:
        print(f"Erreur Modification NEMO : {e}")
        raise HTTPException(status_code=500, detail=str(e))

# 3. TÉLÉCHARGEMENT ZIP
@app.get("/api/download/{project_id}")
async def download_project_zip(project_id: str, background_tasks: BackgroundTasks):
    clean_id = re.sub(r'[^\w\-]', '_', project_id).lower()
    project_dir = os.path.join(PROJECTS_DIR, clean_id)
    
    if not os.path.exists(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable sur le serveur.")
    
    zip_path = os.path.join(PROJECTS_DIR, f"{clean_id}.zip")
    shutil.make_archive(os.path.join(PROJECTS_DIR, clean_id), 'zip', project_dir)
    
    # Programme la suppression du fichier ZIP temporaire après l'envoi
    background_tasks.add_task(remove_file, zip_path)
    
    return FileResponse(path=zip_path, filename=f"{clean_id}.zip", media_type='application/zip')
        
