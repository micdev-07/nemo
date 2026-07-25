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
app = FastAPI(title="NEMO Studio API", version="2.1.0")

# Support du CORS (Configuration stricte sans slash final)
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
# --- PROMPT SYSTEME (SPECIALISTE FRONT-END) ---
SYSTEM_PROMPT_NEMO = """
Tu es NEMO, un développeur Front-end d'élite et UI/UX Designer d'exception.
Ton objectif est de concevoir des applications web Single Page (SPA), des portfolios, des landing pages et des outils web d'une qualité visuelle et fonctionnelle "Masterclass" (niveau Awwwards).

REGLES ET EXPERTISE TECHNIQUE :
1. PURE FRONT-END : Génère uniquement du HTML, CSS et JavaScript côté client. N'utilise AUCUN backend, ni API externe nécessitant des clés d'accès.
2. PERSISTANCE LOCALE (`localStorage`) : Si l'application nécessite de sauvegarder un état, utilise exclusivement le `localStorage`.
3. EXCELLENCE VISUELLE & UI/UX (SUBTILE ET ÉLÉGANTE) :
   - Design ultra moderne (Dark Mode premium, glassmorphism, typographies Google Fonts, icônes SVG).
   - EFFET NÉON / GLOW SUBTIL : Si un effet néon ou lumineux est demandé, il doit être LÉGER, DISCRET et ÉLÉGANT. Utilise des opacités réduites (ex: `rgba(..., 0.2)` ou `0.3`) pour éviter d'agresser les yeux ou de gêner la lisibilité du texte. Évite le `text-shadow` surchargé.
   - Layout 100% Responsive et animations fluides (`@keyframes` légers).
   - Code complet, propre, réactif, sans placeholders.
4. CHEMINS RELATIFS OBLIGATOIRES : Dans `index.html`, lie TOUJOURS les fichiers de style et scripts avec des chemins relatifs simples, sans slash initial (ex: `href="style.css"` et NOT `href="/style.css"`).
5. FORMAT DE RÉPONSE STRICT :
   Réponds EXCLUSIVEMENT sous la forme d'un objet JSON valide contenant la clé "files" avec tous les fichiers du projet (ex: index.html, style.css, script.js).
"""


# SCHÉMA DE RÉPONSE STRUCTURÉ STRICT POUR GEMINI
# SCHÉMA DE RÉPONSE COMPATIBLE GEMINI DEVELOPER API
JSON_SCHEMA_NEMO = {
    "type": "OBJECT",
    "properties": {
        "files": {
            "type": "OBJECT",
            "properties": {
                "index.html": {"type": "STRING"},
                "style.css": {"type": "STRING"},
                "script.js": {"type": "STRING"}
            }
        }
    },
    "required": ["files"]
}


# --- MODÈLES DE REQUÊTE ---
class CreateProjectRequest(BaseModel):
    prompt: str

class ModifyProjectRequest(BaseModel):
    project_id: str
    prompt: str
    current_files: Dict[str, str]

# --- PARSEUR JSON ULTRA-ROBUSTE ---

def parse_llm_json(raw_text: str) -> dict:
    """
    Extrait et répare le JSON renvoyé par Gemini même s'il contient 
    des caractères d'échappement problématiques ou des balises Markdown.
    """
    cleaned = raw_text.strip()
    
    # 1. Suppression du balisage Markdown (```json ... ```)
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    # 2. Tentative de parse direct
    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        pass

    # 3. Extraction du bloc JSON { ... } principal avec Regex
    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str, strict=False)
        except json.JSONDecodeError:
            # 4. Secours : correction des retours à la ligne non échappés dans les chaînes HTML/CSS
            try:
                json_str_fixed = re.sub(r'(?<!\\)\n', r'\\n', json_str)
                return json.loads(json_str_fixed, strict=False)
            except json.JSONDecodeError:
                pass

    raise ValueError("Impossible de parser la structure JSON retournée par l'IA.")

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
    return {"status": "online", "system": "NEMO Studio Engine v2.1"}

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
                response_schema=JSON_SCHEMA_NEMO,
                temperature=0.3,
                max_output_tokens=8192
            )
        )
        
        # Extraction du JSON
        project_data = parse_llm_json(response.text)
        files = project_data.get("files", {})
        
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
                response_schema=JSON_SCHEMA_NEMO,
                temperature=0.4,
                max_output_tokens=8192
            )
        )
        
        # Extraction du JSON
        project_data = parse_llm_json(response.text)
        files = project_data.get("files", {})
        
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
    
    background_tasks.add_task(remove_file, zip_path)
    
    return FileResponse(path=zip_path, filename=f"{clean_id}.zip", media_type='application/zip')
        
