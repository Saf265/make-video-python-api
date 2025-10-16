from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import ffmpeg
import tempfile
import os
import io
from typing import List

app = FastAPI(title="YouTube Video Cutter API")

# Configuration CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",  # Next.js dev
        "http://localhost:3001",  # Au cas où
        "https://ton-app.vercel.app",  # Production (remplace par ton domaine)
    ],
    allow_credentials=True,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

class VideoCutRequest(BaseModel):
    timeCode: List[str]  # Format: ["00:00:10", "00:00:30"]

def time_to_seconds(time_str: str) -> float:
    """Convert time string (HH:MM:SS) to seconds"""
    parts = time_str.split(':')
    if len(parts) == 3:
        hours, minutes, seconds = map(float, parts)
        return hours * 3600 + minutes * 60 + seconds
    elif len(parts) == 2:
        minutes, seconds = map(float, parts)
        return minutes * 60 + seconds
    else:
        return float(parts[0])

@app.post("/cut-video")
async def cut_video(
    video_file: UploadFile = File(...),
    timeCode: str = Form(...)  # Format: "00:00:10,00:00:30"
):
    """
    Découpe une vidéo MP4 uploadée selon le timeCode fourni
    """
    try:
        # Validation du fichier
        if not video_file.content_type or not video_file.content_type.startswith('video/'):
            raise HTTPException(status_code=400, detail="Le fichier doit être une vidéo")
        
        # Parser le timeCode
        try:
            # Supprimer les crochets et espaces, puis split par virgule
            clean_timecode = timeCode.strip('[]').replace(' ', '')
            time_parts = clean_timecode.split(',')
            
            if len(time_parts) != 2:
                raise ValueError("Format invalide")
                
            start_time, end_time = time_parts
            start_seconds = time_to_seconds(start_time)
            end_seconds = time_to_seconds(end_time)
            
        except (ValueError, IndexError):
            raise HTTPException(status_code=400, detail="timeCode doit être au format '[00:00:10,00:00:30]' ou '00:00:10,00:00:30'")
        
        if start_seconds >= end_seconds:
            raise HTTPException(status_code=400, detail="Le timestamp de début doit être inférieur au timestamp de fin")
        
        # Créer un dossier temporaire
        with tempfile.TemporaryDirectory() as temp_dir:
            # Sauvegarder le fichier uploadé
            input_file = os.path.join(temp_dir, f"input_{video_file.filename}")
            with open(input_file, "wb") as f:
                content = await video_file.read()
                f.write(content)
            
            # Nom du fichier de sortie
            base_name = os.path.splitext(video_file.filename or "video")[0]
            safe_name = "".join(c for c in base_name if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_name = safe_name[:30]  # Limiter la longueur
            
            output_file = os.path.join(temp_dir, f"cut_{safe_name}.mp4")
            filename = f"cut_{safe_name}_{start_seconds}s-{end_seconds}s.mp4"
            
            # Découper la vidéo avec ffmpeg
            duration = end_seconds - start_seconds
            
            try:
                (
                    ffmpeg
                    .input(input_file, ss=start_seconds, t=duration)
                    .output(output_file, vcodec='libx264', acodec='aac', preset='fast')
                    .overwrite_output()
                    .run(quiet=True, capture_stdout=True, capture_stderr=True)
                )
            except ffmpeg.Error as e:
                error_msg = e.stderr.decode() if e.stderr else "Erreur ffmpeg inconnue"
                raise HTTPException(status_code=500, detail=f"Erreur lors du découpage: {error_msg}")
            
            # Vérifier que le fichier de sortie existe
            if not os.path.exists(output_file):
                raise HTTPException(status_code=500, detail="Échec de la création du fichier découpé")
            
            # Lire le fichier coupé en mémoire
            with open(output_file, 'rb') as f:
                video_data = f.read()
            
            # Retourner le blob avec filename
            return StreamingResponse(
                io.BytesIO(video_data),
                media_type="video/mp4",
                headers={
                    "Content-Disposition": f"attachment; filename={filename}"
                }
            )
    
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Erreur lors du traitement: {str(e)}")

@app.get("/")
async def root():
    return {
        "message": "Video Cutter API", 
        "usage": "POST /cut-video avec video_file (MP4) et timeCode '[00:00:10,00:00:30]'"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)