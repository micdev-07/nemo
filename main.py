import os
import re
import json
import time
import shutil
import requests
from datetime import date
from collections import defaultdict
from typing import Dict
from fastapi import FastAPI, HTTPException, BackgroundTasks
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types

# --- CONFIGURATION INITIALE ---
app = FastAPI(title="NEMO Studio API", version="2.2.0")

# Suivi du quota de modification (2 modifs max / jour / projet)
DAILY_MODIFY_LIMIT = 2
usage_tracker = defaultdict(lambda: defaultdict(int))

# Support du CORS
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

# SDK Gemini et Variables d'environnement
client = genai.Client()
BACKEND_URL = os.getenv("BACKEND_URL", "https://nemo-hdgw.onrender.com")
NETLIFY_AUTH_TOKEN = os.getenv("NETLIFY_AUTH_TOKEN", "")

# --- PROMPT SYSTEME (SPECIALISTE FRONT-END) ---
SYSTEM_PROMPT_NEMO = """
Tu es NEMO, un développeur Front-end d'élite et UI/UX Designer d'exception.
Ton objectif est de concevoir des applications web Single Page (SPA), des portfolios, des landing pages et des outils web d'une qualité visuelle et fonctionnelle "Masterclass" (niveau Awwwards).
Dans tout le projet que tu vas générer mentionne que c'est créé par l'outil NEMO STUDIO de Micoffice Labs (sans agresser le rendu final du site).
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

class DeployProjectRequest(BaseModel):
    project_id: str

# --- PARSEUR JSON ULTRA-ROBUSTE ---
def parse_llm_json(raw_text: str) -> dict:
    """Extrait et répare le JSON renvoyé par Gemini."""
    cleaned = raw_text.strip()
    cleaned = re.sub(r'^```(?:json)?\s*', '', cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r'\s*```$', '', cleaned)
    cleaned = cleaned.strip()

    try:
        return json.loads(cleaned, strict=False)
    except json.JSONDecodeError:
        pass

    match = re.search(r'\{.*\}', cleaned, re.DOTALL)
    if match:
        json_str = match.group(0)
        try:
            return json.loads(json_str, strict=False)
        except json.JSONDecodeError:
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
    """Nettoyage en arrière-plan des fichiers temporaires."""
    try:
        if os.path.exists(path): 
            os.remove(path)
    except Exception as e:
        print(f"Erreur lors du nettoyage : {e}")

# --- ROUTES API ---

@app.get("/")
async def root():
    return {"status": "online", "system": "NEMO Studio Engine v2.2"}

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
                temperature=0.1,
                max_output_tokens=8192
            )
        )
        raw_text = response.text

        print("=" * 40)
        print("=== RÉPONSE BRUTE DE GEMINI ===")
        print(raw_text)
        print("=" * 40)

        project_data = parse_llm_json(raw_text)
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
        err_msg = str(e)
        if "429" in err_msg or "RESOURCE_EXHAUSTED" in err_msg:
            raise HTTPException(
                status_code=429, 
                detail="NEMO est surchargé ! Trop de demandes en peu de temps, réessaye dans 10 secondes."
            )
        print(f"Erreur NEMO : {e}")
        raise HTTPException(status_code=500, detail=f"Erreur interne : {err_msg}")

# 2. MODIFICATION DE PROJET
@app.post("/api/modify")
async def modify_project(request: ModifyProjectRequest):
    today_str = str(date.today())
    project_id = request.project_id
    
    # 1. Vérification du quota journalier (Max 2 modifs par projet)
    current_count = usage_tracker[today_str][project_id]
    if current_count >= DAILY_MODIFY_LIMIT:
        raise HTTPException(
            status_code=429,
            detail=f"Limite atteinte : Vous ne pouvez modifier un même projet que {DAILY_MODIFY_LIMIT} fois par jour."
        )

    # 2. Construction du prompt optimisé
    full_prompt = (
        f"{SYSTEM_PROMPT_NEMO}\n\n"
        f"--- CODE ACTUEL DU PROJET ---\n{json.dumps(request.current_files, ensure_ascii=False)}\n\n"
        f"--- MODIFICATION DEMANDÉE ---\n{request.prompt}"
    )

    # 3. Exécution avec gestion des réessais en cas de Rate Limit (429)
    max_retries = 3
    for attempt in range(max_retries):
        try:
            print(f"🔄 Tentative de modification {attempt + 1}/{max_retries} pour le projet {project_id}...")
            
            response = client.models.generate_content(
                model='gemini-2.5-flash',
                contents=full_prompt,
                config=types.GenerateContentConfig(
                    response_mime_type="application/json",
                    response_schema=JSON_SCHEMA_NEMO,
                    temperature=0.1,
                    max_output_tokens=8192
                )
            )
            raw_text = response.text

            print("=" * 40)
            print("=== RÉPONSE BRUTE DE GEMINI (MODIFY) ===")
            print(raw_text)
            print("=" * 40)

            parsed_data = parse_llm_json(raw_text)
            files = parsed_data.get("files", {})

            if not files or not isinstance(files, dict):
                raise ValueError("Format de réponse invalide pour la modification.")

            # Sauvegarde sur le disque
            clean_id = save_project_to_disk(project_id, files)
            project_url = f"{BACKEND_URL}/projects/{clean_id}/index.html?t={int(time.time())}"
            
            # 4. Incrémentation du compteur
            usage_tracker[today_str][project_id] += 1
            remaining = DAILY_MODIFY_LIMIT - usage_tracker[today_str][project_id]
            print(f"✅ Modification réussie. Modifications restantes pour aujourd'hui : {remaining}")

            return {
                "status": "success",
                "project_id": clean_id,
                "project_url": project_url,
                "files": files,
                "remaining_modifications": remaining
            }

        except ResourceExhausted:
            if attempt < max_retries - 1:
                print("⚠️ Rate Limit atteint. Pause de 5 secondes avant de réessayer...")
                time.sleep(5)
            else:
                raise HTTPException(
                    status_code=429,
                    detail="L'IA est temporairement trop sollicitée. Réessaie dans une minute."
                )
        except Exception as e:
            print(f"❌ Erreur lors de la modification : {str(e)}")
            raise HTTPException(
                status_code=500,
                detail=f"Erreur interne lors de la modification : {str(e)}"
            )

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

# 4. DÉPLOIEMENT PERMANENT SUR NETLIFY
@app.post("/api/deploy")
async def deploy_to_netlify(req: DeployProjectRequest, background_tasks: BackgroundTasks):
    if not NETLIFY_AUTH_TOKEN:
        raise HTTPException(status_code=500, detail="Token d'authentification Netlify non configuré.")
    
    clean_id = re.sub(r'[^\w\-]', '_', req.project_id).lower()
    project_dir = os.path.join(PROJECTS_DIR, clean_id)
    
    if not os.path.exists(project_dir):
        raise HTTPException(status_code=404, detail="Projet introuvable pour le déploiement.")

    zip_base_path = os.path.join(PROJECTS_DIR, f"deploy_{clean_id}")
    zip_path = f"{zip_base_path}.zip"
    shutil.make_archive(zip_base_path, 'zip', project_dir)
    
    background_tasks.add_task(remove_file, zip_path)

    try:
        headers = {
            "Authorization": f"Bearer {NETLIFY_AUTH_TOKEN}",
            "Content-Type": "application/zip"
        }
        
        url = "https://api.netlify.com/api/v1/sites"
        
        with open(zip_path, "rb") as f:
            res = requests.post(url, headers=headers, data=f)

        if res.status_code not in [200, 201]:
            print(f"Netlify API Error: {res.status_code} - {res.text}")
            raise HTTPException(status_code=500, detail="Échec lors du déploiement sur Netlify.")

        data = res.json()
        deployed_url = data.get("ssl_url") or data.get("url")

        return {
            "status": "success",
            "message": "Projet déployé avec succès sur Netlify !",
            "deploy_url": deployed_url
        }

    except Exception as e:
        print(f"Erreur Déploiement : {e}")
        raise HTTPException(status_code=500, detail=str(e))
            
