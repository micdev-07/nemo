import os
import re
import json
import time
from typing import Dict
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from google import genai
from google.genai import types

# --- CONFIGURATION INITIALE ---
app = FastAPI(title="NEMO Studio API (Ultra-Light)", version="2.5.0")

# Support du CORS élargi/souple pour Netlify
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "https://nemostudio.netlify.app",
        "https://studio.netlify.app",
        "http://localhost:3000",
        "*"
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialisation du client SDK Gemini
client = genai.Client()

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

# --- ROUTES API ---

@app.get("/")
async def root():
    return {"status": "online", "system": "NEMO Studio Engine v2.5 (100% Stateless)"}

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
                raise ValueError("Impossible de parser le JSON retourné par l'IA.")

        files = project_data.get("files", project_data) if isinstance(project_data, dict) else {}
        if not files or not isinstance(files, dict):
            raise ValueError("Aucun dictionnaire de fichiers valide reçu.")
        
        return {
            "status": "success",
            "project_id": project_id,
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
                raise ValueError("Impossible de parser le JSON retourné par l'IA.")

        # Extraction sécurisée des fichiers
        if "files" in project_data and isinstance(project_data["files"], dict):
            files = project_data["files"]
        elif isinstance(project_data, dict):
            files = project_data
        else:
            files = {}

        if not files:
            raise ValueError("Aucun dictionnaire de fichiers valide reçu.")

        return {
            "status": "success",
            "project_id": req.project_id,
            "files": files
        }

    except Exception as e:
        print(f"Erreur Modification NEMO : {e}")
        raise HTTPException(status_code=500, detail=str(e))
        
