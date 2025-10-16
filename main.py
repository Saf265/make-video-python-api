from fastapi import FastAPI, HTTPException, File, UploadFile, Form
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
import ffmpeg
import tempfile
import os
import io
from typing import List, Optional

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
    timeCode: str = Form(...),  # Format: "00:00:10,00:00:30"
    video_file: Optional[UploadFile] = File(None),
    youtubeVideoUrl: Optional[str] = Form(None)
):
    """
    Découpe une vidéo (uploadée ou YouTube) selon le timeCode fourni
    """
    try:
        # Validation : il faut soit un fichier, soit une URL YouTube
        if not video_file and not youtubeVideoUrl:
            raise HTTPException(status_code=400, detail="Il faut fournir soit video_file soit youtubeVideoUrl")
        
        if video_file and youtubeVideoUrl:
            raise HTTPException(status_code=400, detail="Fournir soit video_file soit youtubeVideoUrl, pas les deux")
        
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
            
            if youtubeVideoUrl:
                # Télécharger depuis YouTube
                ydl_opts = {
                    'format': 'worst[ext=mp4]/worst',
                    'outtmpl': os.path.join(temp_dir, '%(title)s.%(ext)s'),
                    'no_warnings': False,
                    'extractaudio': False,
                    'ignoreerrors': True,
                    'writesubtitles': False,
                    'writeautomaticsub': False,
                    'extractor_args': {
                        'youtube': {
                            'skip': ['hls', 'dash'],
                            'player_skip': ['configs', 'webpage']
                        }
                    },
                    'http_headers': {
                        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                    },
                }
                
                try:
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                        info = ydl.extract_info(youtubeVideoUrl, download=False)
                        video_title = info.get('title', 'youtube_video')
                        ydl.download([youtubeVideoUrl])
                        
                        # Trouver le fichier téléchargé
                        downloaded_files = [f for f in os.listdir(temp_dir) if f.endswith(('.mp4', '.webm', '.mkv'))]
                        if not downloaded_files:
                            raise HTTPException(status_code=500, detail="Échec du téléchargement YouTube")
                        
                        input_file = os.path.join(temp_dir, downloaded_files[0])
                        safe_name = "".join(c for c in video_title if c.isalnum() or c in (' ', '-', '_')).rstrip()[:30]
                        
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Erreur YouTube: {str(e)}")
            
            else:
                # Fichier uploadé
                if not video_file.content_type or not video_file.content_type.startswith('video/'):
                    raise HTTPException(status_code=400, detail="Le fichier doit être une vidéo")
                
                input_file = os.path.join(temp_dir, f"input_{video_file.filename}")
                with open(input_file, "wb") as f:
                    content = await video_file.read()
                    f.write(content)
                
                base_name = os.path.splitext(video_file.filename or "video")[0]
                safe_name = "".join(c for c in base_name if c.isalnum() or c in (' ', '-', '_')).rstrip()[:30]
            
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
        "usage": "POST /cut-video avec (video_file OU youtubeVideoUrl) et timeCode '[00:00:10,00:00:30]'"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)