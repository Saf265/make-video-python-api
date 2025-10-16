from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import yt_dlp
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

class VideoRequest(BaseModel):
    youtube_url: str
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
async def cut_video(request: VideoRequest):
    """
    Télécharge une vidéo YouTube et retourne un segment coupé selon le timeCode
    """
    try:
        # Validation du timeCode
        if len(request.timeCode) != 2:
            raise HTTPException(status_code=400, detail="timeCode doit contenir exactement 2 timestamps [start, end]")
        
        start_time = time_to_seconds(request.timeCode[0])
        end_time = time_to_seconds(request.timeCode[1])
        
        if start_time >= end_time:
            raise HTTPException(status_code=400, detail="Le timestamp de début doit être inférieur au timestamp de fin")
        
        # Configuration yt-dlp avec cookies pour contourner les restrictions
        ydl_opts = {
            'format': 'worst[ext=mp4]/worst',
            'outtmpl': '%(title)s.%(ext)s',
            'no_warnings': False,
            'extractaudio': False,
            'ignoreerrors': True,
            'writesubtitles': False,
            'writeautomaticsub': False,
            # Utiliser les cookies du navigateur pour éviter la détection de bot
            'cookiesfrombrowser': ('chrome', None, None, None),  # Essaie Chrome en premier
            # Options de fallback
            'extractor_args': {
                'youtube': {
                    'skip': ['hls', 'dash'],
                    'player_skip': ['configs', 'webpage']
                }
            },
            'http_headers': {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            },
        }
        
        # Créer un dossier temporaire
        with tempfile.TemporaryDirectory() as temp_dir:
            # Télécharger la vidéo
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                try:
                    info = ydl.extract_info(request.youtube_url, download=False)
                    video_title = info.get('title', 'video')
                    
                    # Vérifier les formats disponibles
                    formats = info.get('formats', [])
                    if not formats:
                        raise HTTPException(status_code=400, detail="Aucun format vidéo disponible pour cette URL")
                    
                    # Télécharger dans le dossier temporaire
                    ydl_opts['outtmpl'] = os.path.join(temp_dir, '%(title)s.%(ext)s')
                    with yt_dlp.YoutubeDL(ydl_opts) as ydl_download:
                        ydl_download.download([request.youtube_url])
                        
                except yt_dlp.DownloadError as e:
                    # Essayer différentes stratégies de cookies
                    if "Sign in to confirm" in str(e) or "bot" in str(e):
                        browsers_to_try = [
                            ('firefox', 'Firefox'),
                            ('edge', 'Edge'),
                            ('safari', 'Safari'),
                            (None, 'Sans cookies')  # Dernier recours
                        ]
                        
                        success = False
                        for browser, browser_name in browsers_to_try:
                            try:
                                alt_opts = ydl_opts.copy()
                                if browser:
                                    alt_opts['cookiesfrombrowser'] = (browser, None, None, None)
                                else:
                                    # Sans cookies, avec options agressives
                                    alt_opts.pop('cookiesfrombrowser', None)
                                    alt_opts.update({
                                        'format': 'best[height<=360]/worst',
                                        'extractor_args': {
                                            'youtube': {
                                                'skip': ['hls', 'dash', 'translated_subs'],
                                                'player_skip': ['js', 'configs', 'webpage']
                                            }
                                        }
                                    })
                                
                                with yt_dlp.YoutubeDL(alt_opts) as ydl_alt:
                                    info = ydl_alt.extract_info(request.youtube_url, download=False)
                                    video_title = info.get('title', 'video')
                                    alt_opts['outtmpl'] = os.path.join(temp_dir, '%(title)s.%(ext)s')
                                    ydl_alt.download([request.youtube_url])
                                    success = True
                                    break
                            except:
                                continue
                        
                        if not success:
                            raise HTTPException(status_code=400, detail="Cette vidéo nécessite une authentification ou n'est pas accessible. Essayez avec une autre vidéo publique.")
                    else:
                        raise HTTPException(status_code=400, detail=f"Erreur de téléchargement YouTube: {str(e)}")
                except Exception as e:
                    raise HTTPException(status_code=400, detail=f"Erreur lors de l'extraction des infos: {str(e)}")
            
            # Trouver le fichier téléchargé
            downloaded_files = [f for f in os.listdir(temp_dir) if f.endswith(('.mp4', '.webm', '.mkv'))]
            if not downloaded_files:
                raise HTTPException(status_code=500, detail="Échec du téléchargement de la vidéo")
            
            input_file = os.path.join(temp_dir, downloaded_files[0])
            output_file = os.path.join(temp_dir, f"cut_{video_title}.mp4")
            
            # Nettoyer le nom du fichier pour le filename
            safe_title = "".join(c for c in video_title if c.isalnum() or c in (' ', '-', '_')).rstrip()
            safe_title = safe_title[:50]  # Limiter la longueur
            filename = f"cut_{safe_title}_{start_time}s-{end_time}s.mp4"
            
            # Découper la vidéo avec ffmpeg
            duration = end_time - start_time
            
            (
                ffmpeg
                .input(input_file, ss=start_time, t=duration)
                .output(output_file, vcodec='libx264', acodec='aac')
                .overwrite_output()
                .run(quiet=True)
            )
            
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
        "message": "YouTube Video Cutter API", 
        "usage": "POST /cut-video avec youtube_url et timeCode [start, end]"
    }

@app.get("/health")
async def health_check():
    return {"status": "healthy"}

if __name__ == "__main__":
    import uvicorn
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run(app, host="0.0.0.0", port=port)