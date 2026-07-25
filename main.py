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

# Support du CORS pour connecter facilement ton Front-end
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dossier local de stockage temporaire des projets rendus
PROJECTS_DIR = "projects"
os.makedirs(PROJECTS_DIR, exist_ok=True)
app.mount("/projects", StaticFiles(directory=PROJECTS_DIR), name="projects")

# Initialisation du client SDK Gemini (utilise la variable d'environnement GEMINI_API_KEY)
client = genai.Client()
BACKEND_URL = os.getenv("BACKEND_URL", "https://nemo-hdgw.onrender.com")

# --- PROMPT SYSTÈME (SPÉCIALISTE FRONT-END & UI/UX) ---
SYSTEM_PROMPT_NEMO = """
Tu es NEMO, un développeur Front-end d'élite et UI/UX Designer d'exception.
Ton objectif est de concevoir des applications web Single Page (SPA), des portfolios, des landing pages et des outils web d'une qualité visuelle et fonctionnelle "Masterclass" (niveau Awwwards).

REGLES ET EXPERTISE TECHNIQUE :
1. PURE FRONT-END : Génère uniquement du HTML, CSS et JavaScript côté client. N'utilise AUCUN backend, ni API externe nécessitant des clés d'accès.
2. PERSISTANCE LOCALE (`localStorage`) : Si l'application nécessite de sauvegarder un état (score de jeu, panier fictif, préférences, tâches, formulaire), utilise exclusivement le `localStorage` du navigateur.
3. EXCELLENCE VISUELLE & UI/UX :
   - Design ultra moderne (gradients subtils, glassmorphism, animations fluides `@keyframes`, typographies Google Fonts, icônes SVG/Lucide).
   - Layout 100% Responsive (Mobile-First, Flexbox, CSS Grid).
   - Code complet, propre, réactif, sans placeholders ni commentaires TODO.
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

# --- FONCTIONS UTILITAIRES ---
def save_project_to_disk(project_id: str, files: dict) -> str:
    """Nettoie l'ID du projet et enregistre/écrase les fichiers sur le disque."""
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

def clean_json_response(raw_text: str) -> str:
    """Nettoie les balises markdown ```json éventuelles renvoyées par le LLM."""
    cleaned = raw_text.strip()
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    elif cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    return cleaned.strip()

def remove_file(path: str):
    """Fonction de nettoyage en arrière-plan pour supprimer le ZIP temporaire."""
    try:
        if os.path.exists(path):
            os.remove(path)
    except Exception as e:
        print(f"Erreur lors de la suppression du ZIP temporaire : {e}")

# --- ROUTES API ---

@app.get("/")
async def root():
    return {"status": "online", "system": "NEMO Studio Frontend Engine v2.0"}

# 1. CRÉATION D'UN PROJET
@app.post("/api/create")
async def create_project(req: CreateProjectRequest):
    try:
        project_id = f"nemo_{int(time.time())}"
        
        prompt_create = f"""
{SYSTEM_PROMPT_NEMO}

DEMANDE DE L'UTILISATEUR :
{req.prompt}

CONSIGNE : Crée une application web / site front-end d'un niveau visuel exceptionnel ("Masterclass").
"""
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt_create,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.4,
                max_output_tokens=8192
            )
        )
        
        cleaned_text = clean_json_response(response.text)
        
        try:
            project_data = json.loads(cleaned_text, strict=False)
        except Exception:
            match = re.search(r'\{.*\}', response.text, re.DOTALL)
            if match:
                project_data = json.loads(match.group(0), strict=False)
            else:
                raise ValueError("Impossible de parser le JSON retourné.")

        files = project_data.get("files", project_data) if isinstance(project_data, dict) else {}
        if not files or not isinstance(files, dict):
            raise ValueError("Aucun dictionnaire de fichiers valide reçu.")

        save_project_to_disk(project_id, files)
        
        timestamp = int(time.time())
        project_url = f"{BACKEND_URL}/projects/{project_id}/index.html?t={timestamp}"
        
        return {
            "status": "success",
            "project_id": project_id,
            "project_url": project_url,
            "files": files
        }

    except Exception as e:
        print(f"Erreur Création NEMO : {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 2. MODIFICATION D'UN PROJET EXISTANT
@app.post("/api/modify")
async def modify_project(req: ModifyProjectRequest):
    try:
        prompt_modif = f"""
{SYSTEM_PROMPT_NEMO}

PROJET ACTUEL AU FORMAT JSON :
{json.dumps(req.current_files)}

MODIFICATIONS DEMANDÉES PAR L'UTILISATEUR :
{req.prompt}

DIRECTIVE STRICTE :
Conserve la qualité visuelle 'Masterclass'. Intègre la modification demandée sans dégrader le style existant et sans supprimer le travail précédent.
"""
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt_modif,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.4,
                max_output_tokens=8192
            )
        )
        
        cleaned_text = clean_json_response(response.text)
        
        try:
            project_data = json.loads(cleaned_text, strict=False)
        except Exception:
            match = re.search(r'\{.*\}', response.text, re.DOTALL)
            if match:
                project_data = json.loads(match.group(0), strict=False)
            else:
                raise ValueError("Impossible de parser le JSON retourné.")

        # Extraction sécurisée des fichiers
        if "files" in project_data and isinstance(project_data["files"], dict):
            files = project_data["files"]
        elif isinstance(project_data, dict):
            files = project_data
        else:
            files = {}

        if not files:
            raise ValueError("Aucun dictionnaire de fichiers valide reçu.")

        # Sauvegarde/Écrasement sur disque
        project_id = save_project_to_disk(req.project_id, files)
        
        # Lien avec paramètre anti-cache (?t=)
        timestamp = int(time.time())
        project_url = f"{BACKEND_URL}/projects/{project_id}/index.html?t={timestamp}"
        
        return {
            "status": "success",
            "project_id": project_id,
            "project_url": project_url,
            "files": files
        }

    except Exception as e:
        print(f"Erreur Modification NEMO : {e}")
        raise HTTPException(status_code=500, detail=str(e))


# 3. TÉLÉCHARGEMENT DU ZIP
@app.get("/api/download/{project_id}")
async def download_project_zip(project_id: str, background_tasks: BackgroundTasks):
    clean_id = re.sub(r'[^\w\-]', '_', project_id).lower()
    project_dir = os.path.join(PROJECTS_DIR, clean_id)
    
    if not os.path.exists(project_dir):
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    
    zip_filename = f"{clean_id}.zip"
    zip_path = os.path.join(PROJECTS_DIR, zip_filename)
    
    # Création de l'archive ZIP à partir du dossier projet mis à jour
    shutil.make_archive(os.path.join(PROJECTS_DIR, clean_id), 'zip', project_dir)
    
    # Suppression automatique du fichier zip sur le serveur après le téléchargement
    background_tasks.add_task(remove_file, zip_path)
    
    return FileResponse(
        path=zip_path, 
        filename=zip_filename, 
        media_type='application/zip'
    )
