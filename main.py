import os
import json
import re
import shutil
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse
from pydantic import BaseModel
from google import genai
from google.genai import types
from dotenv import load_dotenv

load_dotenv()
def clean_json_response(text: str) -> str:
    # 1. Retirer le wrapper markdown ```json ... ```
    text = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
    text = re.sub(r"^```\s*", "", text, flags=re.MULTILINE)
    text = text.strip()
    
    # 2. Nettoyer les faux échappements quadruplés (\\\\') sans casser le JS
    text = text.replace("\\\\'", "'").replace("\\'", "'")
    
    return text
app = FastAPI(title="NEMO Engine - Full Featured")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://leafy-stardust-02122c.netlify.app"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

os.makedirs("output_projects", exist_ok=True)
app.mount("/projects", StaticFiles(directory="output_projects"), name="projects")

client = genai.Client(api_key=os.getenv("GEMINI_API_KEY"))

SUPABASE_URL = os.getenv("SUPABASE_URL", "")
SUPABASE_ANON_KEY = os.getenv("SUPABASE_ANON_KEY", "")

class NewProjectRequest(BaseModel):
    prompt: str

class ModifyProjectRequest(BaseModel):
    project_id: str
    prompt: str
    current_files: dict

SYSTEM_PROMPT_NEMO = f"""
Tu es NEMO, un agent d'action IA et Lead Designer / Architecte Logiciel Fullstack développé par Micoffice Labs.
Ton rôle est de concevoir et modifier tout type d'application web moderne, immersive, riche et totalement fonctionnelle (E-commerce, SaaS, Dashboard, Canvas, Outils, Jeux, Portfolios, etc.).

INTERDICTION STRICTE :
- Ne génère JAMAIS d'application de type Réseau Social (fil d'actualité, posts type Twitter/Facebook/Instagram).

DIRECTIVES D'EXCELLENCE :

1. GESTION DES IMAGES & MÉDIAS :
   - Si le site/projet nécessite des images (ex: produits e-commerce, avatars, bannières, cartes de contenu) :
   - Récupère et intègre TOUJOURS des images réelles, esthétiques et directement pertinentes.
   - Utilise des URLs d'images directes provenant d'Unsplash (`https://images.unsplash.com/...`), Pexels ou Picsum (`https://picsum.photos/...`).
   - Ne laisse JAMAIS de conteneurs d'images vides ou de simples placeholders génériques.

2. DESIGN & ERGONOMIE :
   - Interfaces modernes : Thème sombre ou clair adapté, Glassmorphism, néons pas génants, animations CSS fluides, responsive design.

3. PERSISTANCE DE DONNÉES (SUPABASE & LOCALSTORAGE) :
   - Si besoin de données dynamiques (panier, tâches, produits, réglages, formulaires) :
     - Inclus le SDK Supabase dans `index.html` :
       `<script src="https://cdn.jsdelivr.net/npm/@supabase/supabase-js@2"></script>`
     - Initialise Supabase dans `app.js` avec :
       `const SUPABASE_URL = "{SUPABASE_URL}";`
       `const SUPABASE_ANON_KEY = "{SUPABASE_ANON_KEY}";`
       `const supabase = supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);`
     - Mets en place la logique d'interaction BDD (avec fallback automatique sur `localStorage`).

4. FORMAT DE SORTIE STRICT :
   Réponds EXCLUSIVEMENT sous la forme d'un objet JSON valide sans aucune balise markdown :

CONSIGNE D'ARCHITECTURE MULTI-FICHIERS (CRITIQUE) :
- Ne te limite PAS à un seul fichier HTML ou JS monolithique.
- Découpe le projet en autant de fichiers que nécessaire pour garantir un fonctionnement complet sans code tronqué.
- Tu peux créer plusieurs pages HTML (ex: index.html, patients.html, appointments.html) OU une structure modulaire avec plusieurs scripts JS (ex: js/app.js, js/patients.js, js/chart.js).
- Assure-toi que tous les liens du menu (href) et les scripts (src) pointent vers des fichiers réels existants dans ton objet JSON.

FORMAT DE SORTIE STRICT (JSON) :
Réponds EXCLUSIVEMENT sous la forme d'un objet JSON valide :

{{
  "project_name": "nom_du_projet",
  "files": {{
    "index.html": "<... HTML5 principal ...>",
    "patients.html": "<... Page patients ...>",
    "styles/main.css": "<... Styles CSS modernes ...>",
    "js/main.js": "<... JS principal ...>",
    "js/patients.js": "<... Logique patients ...>"
  }}
}}
"""

def save_project_to_disk(project_id, files):
    clean_id = re.sub(r'[^\w\-]', '_', project_id).lower()
    output_dir = os.path.join("output_projects", clean_id)
    os.makedirs(output_dir, exist_ok=True)
    
    for file_path, content in files.items():
        # Sécuriser le chemin pour éviter la traversée de répertoire (ex: ../)
        clean_relative_path = os.path.normpath(file_path).lstrip("\\/")
        full_file_path = os.path.join(output_dir, clean_relative_path)
        
        # Créer les sous-dossiers automatiquement (ex: output_projects/mon_projet/js/pages/)
        os.makedirs(os.path.dirname(full_file_path), exist_ok=True)
        
        with open(full_file_path, "w", encoding="utf-8") as f:
            f.write(content)
            
    return clean_id

@app.get("/api/projects")
async def list_projects():
    projects = []
    output_dir = "output_projects"
    
    if os.path.exists(output_dir):
        for folder in os.listdir(output_dir):
            folder_path = os.path.join(output_dir, folder)
            if os.path.isdir(folder_path):
                # Lire les fichiers du projet
                project_files = {}
                for root, _, files in os.walk(folder_path):
                    for file in files:
                        if not file.endswith('.zip'):
                            full_path = os.path.join(root, file)
                            rel_path = os.path.relpath(full_path, folder_path).replace("\\", "/")
                            try:
                                with open(full_path, "r", encoding="utf-8") as f:
                                    project_files[rel_path] = f.read()
                            except Exception:
                                pass
                
                if "index.html" in project_files:
                    projects.append({
                        "id": folder,
                        "name": folder.replace("_", " ").title(),
                        "url": f"/projects/{folder}/index.html",
                        "files": project_files
                    })
                    
    return {"projects": projects}

@app.post("/api/create")
async def create_project(req: NewProjectRequest):
    try:
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=f"{SYSTEM_PROMPT_NEMO}\n\nCRÉATION DE PROJET : {req.prompt}",
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.4
            )
        )
        
        raw_text = response.text
        cleaned_text = clean_json_response(raw_text)
        
        try:
            project_data = json.loads(cleaned_text, strict=False)
        except Exception as parse_err:
            print(f"Échec parsing direct, tentative d'extraction JSON... Error: {parse_err}")
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if match:
                project_data = json.loads(match.group(0), strict=False)
            else:
                raise parse_err
        project_id = save_project_to_disk(
            project_data.get("project_name", "projet_nemo"), 
            project_data.get("files", {})
        )
        
        project_url = f"http://127.0.0.1:8000/projects/{project_id}/index.html"
        
        return {
            "status": "success",
            "project_id": project_id,
            "project_name": project_data.get("project_name", "Projet NEMO"),
            "project_url": project_url,
            "files": project_data.get("files", {})
        }
    except Exception as e:
        print(f"Erreur NEMO : {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/api/modify")
async def modify_project(req: ModifyProjectRequest):
    try:
        prompt_modif = f"""
{SYSTEM_PROMPT_NEMO}

PROJET ACTUEL AU FORMAT JSON :
{json.dumps(req.current_files)}

MODIFICATIONS DEMANDÉES PAR L'UTILISATEUR :
{req.prompt}

Mets à jour l'application en conservant sa cohérence et en appliquant exactement la modification demandée.
"""
        response = client.models.generate_content(
            model='gemini-2.5-flash',
            contents=prompt_modif,
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.3
            )
        )
        
        raw_text = response.text
        cleaned_text = clean_json_response(raw_text)
        
        try:
            project_data = json.loads(cleaned_text, strict=False)
        except Exception as parse_err:
            print(f"Échec parsing direct (Modify), tentative d'extraction JSON... Error: {parse_err}")
            match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if match:
                project_data = json.loads(match.group(0), strict=False)
            else:
                raise parse_err
        save_project_to_disk(req.project_id, project_data.get("files", {}))
        
        project_url = f"http://127.0.0.1:8000/projects/{req.project_id}/index.html"
        
        return {
            "status": "success",
            "project_id": req.project_id,
            "project_url": project_url,
            "files": project_data.get("files", {})
        }
    except Exception as e:
        print(f"Erreur Modification NEMO : {e}")
        raise HTTPException(status_code=500, detail=str(e))

@app.get("/api/download/{project_id}")
async def download_project_zip(project_id: str):
    clean_id = re.sub(r'[^\w\-]', '_', project_id).lower()
    project_dir = os.path.join("output_projects", clean_id)
    
    if not os.path.exists(project_dir):
        raise HTTPException(status_code=404, detail="Projet non trouvé")
    
    zip_filename = f"{clean_id}.zip"
    zip_path = os.path.join("output_projects", zip_filename)
    
    # Création du fichier ZIP
    shutil.make_archive(os.path.join("output_projects", clean_id), 'zip', project_dir)
    
    return FileResponse(
        path=zip_path, 
        filename=zip_filename, 
        media_type='application/zip'
    )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
