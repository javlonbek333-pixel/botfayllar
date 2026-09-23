import asyncio, base64, logging, os, re, shutil, subprocess, tempfile, time, uuid
from pathlib import Path
import yt_dlp
from shazamio import Shazam
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CallbackQueryHandler, CommandHandler, ContextTypes, MessageHandler, filters

BOT_TOKEN=os.getenv("BOT_TOKEN","").strip()
VIDEO_KBPS=310; AUDIO_KBPS=64; MP3_KBPS=128; FPS=30
MAX_UPLOAD_BYTES=100*1024*1024; SEARCH_LIMIT=100; PAGE_SIZE=10; TTL=1800
ROOT=Path("/tmp/telegram_downloader"); ROOT.mkdir(parents=True,exist_ok=True)
CAPTION="@yuklatgbot orqali yuklab olindi"
DOMAINS=("youtube.com","youtu.be","instagram.com","instagr.am","tiktok.com","vm.tiktok.com","facebook.com","fb.watch","ok.ru","odnoklassniki.ru")
URL_RE=re.compile(r"https?://[^\s<>()]+",re.I)
logging.basicConfig(format="%(asctime)s | %(levelname)s | %(message)s",level=logging.INFO)
log=logging.getLogger("bot")

def is_youtube(u): return any(x in u.lower() for x in ("youtube.com/","youtu.be/","youtube-nocookie.com/"))
def get_url(t):
    m=URL_RE.search(t or ""); return m.group(0).rstrip(".,!?)]}>\"'") if m else None
def supported(u):
    try: h=re.sub(r"^www\.","",u.split("://",1)[1].split("/",1)[0].lower())
    except: return False
    return any(h==d or h.endswith("."+d) for d in DOMAINS)
def err(e): return str(e).strip()[-900:] or "Noma'lum xatolik."
def clean(p): shutil.rmtree(p,ignore_errors=True)

def cookie_file(folder):
    b64=os.getenv("YOUTUBE_COOKIES_B64","").strip()
    if not b64: return None
    try: raw=base64.b64decode(b64,validate=False)
    except: return None
    text=None
    for enc in ("utf-8","utf-16","utf-16-le","utf-16-be"):
        try:
            s=raw.decode(enc)
            if "Netscape HTTP Cookie File" in s or "#HttpOnly_" in s: text=s; break
        except: pass
    if not text: return None
    p=folder/"youtube_cookies.txt"; p.write_text(text,encoding="utf-8",newline=""); return str(p)

def ytopts(folder,audio=False,cookies=True):
    o={"outtmpl":str(folder/"%(id)s.%(ext)s"),"noplaylist":True,"quiet":True,"no_warnings":True,"retries":2,"fragment_retries":2,"concurrent_fragment_downloads":8,"socket_timeout":20,"buffersize":1024*1024,"merge_output_format":"mp4","format":"bestaudio[ext=m4a]/bestaudio/best" if audio else "bv*[height<=854]+ba/b[height<=854]/best","js_runtimes":{"node":{}},"remote_components":{"ejs":["github"]},"extractor_args":{"youtubepot-bgutilhttp":{"base_url":os.getenv("POT_PROVIDER_URL","http://127.0.0.1:4416")}}}
    if cookies:
        c=cookie_file(folder)
        if c: o["cookiefile"]=c
    return o

def ytget(url,folder,audio=False):
    with yt_dlp.YoutubeDL(ytopts(folder,audio)) as y: return y.extract_info(url,download=True)

def search(q,n=100):
    o={"quiet":True,"no_warnings":True,"skip_download":True,"extract_flat":True,"playlistend":n,"socket_timeout":15}
    with yt_dlp.YoutubeDL(o) as y: info=y.extract_info(f"ytsearch{n}:{q}",download=False)
    out=[]
    for e in (info or {}).get("entries",[]) or []:
        if not e: continue
        u=e.get("webpage_url") or (f"https://www.youtube.com/watch?v={e.get('id')}" if e.get("id") else e.get("url"))
        if u: out.append({"title":e.get("title") or "Noma'lum qo'shiq","url":u,"duration":e.get("duration"),"channel":e.get("channel") or e.get("uploader") or ""})
        if len(out)>=n: break
    return out

def ff(args,timeout=900):
    p=subprocess.run(["ffmpeg","-y","-hide_banner","-loglevel","error"]+args,capture_output=True,text=True,timeout=timeout)
    if p.returncode: raise RuntimeError((p.stderr or "FFmpeg xatosi")[-1500:])

def size(p):
    try:
        q=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width,height","-of","csv=s=x:p=0",str(p)],capture_output=True,text=True,timeout=20)
        m=re.search(r"(\d+)x(\d+)",q.stdout); return (int(m.group(1)),int(m.group(2))) if m else None
    except: return None

def vf(w,h):
    if h>w: return "scale=w='min(480,iw)':h='min(854,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
    if w>h: return "scale=w='min(854,iw)':h='min(480,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"
    return "scale=w='min(480,iw)':h='min(480,ih)':force_original_aspect_ratio=decrease:force_divisible_by=2"

def video(src,dst):
    s=size(src)
    if not s: raise RuntimeError("Videoning o'lchami aniqlanmadi.")
    ff(["-i",str(src),"-vf",vf(*s),"-r",str(FPS),"-c:v","libx264","-preset","ultrafast","-b:v",f"{VIDEO_KBPS}k","-maxrate",f"{VIDEO_KBPS}k","-bufsize",f"{VIDEO_KBPS*2}k","-pix_fmt","yuv420p","-threads","0","-map","0:v:0","-map","0:a?","-c:a","aac","-b:a",f"{AUDIO_KBPS}k","-ac","2","-ar","44100","-map_metadata","-1","-movflags","+faststart",str(dst)])
    if not dst.exists() or dst.stat().st_size>MAX_UPLOAD_BYTES: raise RuntimeError("Video 100 MB limitdan oshdi yoki hosil bo'lmadi.")

def mp3(src,dst,title=None,artist=None):
    a=["-i",str(src),"-vn","-c:a","libmp3lame","-b:a",f"{MP3_KBPS}k","-ar","44100","-ac","2","-map_metadata","-1"]
    if title: a += ["-metadata",f"title={title}"]
    if artist: a += ["-metadata",f"artist={artist}"]
    a += [str(dst)]; ff(a,600)
    if not dst.exists() or not dst.stat().st_size: raise RuntimeError("MP3 hosil bo'lmadi.")

def downloaded(folder,exts):
    fs=[p for p in folder.rglob("*") if p.is_file() and p.name!="youtube_cookies.txt" and p.stat().st_size>0 and p.suffix.lower() in exts]
    return max(fs,key=lambda p:p.stat().st_mtime) if fs else None

def dur(s):
    if not s: return ""
    s=int(s); return f"{s//60}:{s%60:02d}"

def keyboard(sid,page,items):
    rows=[]; start=page*PAGE_SIZE
    for i,x in enumerate(items[start:start+PAGE_SIZE]):
        t=x["title"]; t=t[:45]+"..." if len(t)>48 else t
        label=f"{start+i+1}. {t}"+(f" [{dur(x.get('duration'))}]" if x.get('duration') else "")
        rows.append([InlineKeyboardButton(label,callback_data=f"searchsong|{sid}|{start+i}")])
    nav=[]
    if page: nav.append(InlineKeyboardButton("◀️ Oldingi",callback_data=f"songpage|{sid}|{page-1}"))
    if start+PAGE_SIZE<len(items): nav.append(InlineKeyboardButton("Keyingi ▶️",callback_data=f"songpage|{sid}|{page+1}"))
    if nav: rows.append(nav)
    return InlineKeyboardMarkup(rows)

async def start(update,context):
    await update.message.reply_text("Media yuklashni boshlash uchun uning havolasini yuboring.\n\nYouTube havolasida VIDEO yoki MP3 tanlash mumkin. Audio/video yuborsangiz, qo'shiqni Shazam orqali aniqlayman.")

async def text_message(update,context):
    t=(update.message.text or "").strip(); u=get_url(t)
    if u and supported(u):
        if is_youtube(u):
            await update.message.reply_text("Kerakli formatni tanlang:",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎬 VIDEO",callback_data=f"ytvideo|{u}"),InlineKeyboardButton("🎵 MP3",callback_data=f"ytmp3|{u}")]]))
        else: await process_url(update,u)
    elif u: await update.message.reply_text("❌ Bu havola qo'llab-quvvatlanmaydi.")
    elif t: await search_text(update,context,t)

async def search_text(update,context,q):
    m=await update.message.reply_text("⏳ Yuklanmoqda...")
    try: r=await asyncio.to_thread(search,q,100)
    except Exception as e: await m.edit_text("❌ Qidirishda xatolik:\n"+err(e)); return
    if not r: await m.edit_text("❌ Qo'shiq topilmadi."); return
    sid=uuid.uuid4().hex[:10]; context.bot_data.setdefault("searches",{})[sid]={"created":time.time(),"results":r}
    await m.edit_text(f"🎵 <b>{q}</b>\n100 tagacha natija • 10 tadan\n\nQo'shiqni tanlang:",parse_mode="HTML",reply_markup=keyboard(sid,0,r))

async def song_page(update,context):
    q=update.callback_query; await q.answer(); _,sid,p=q.data.split("|",2); d=context.bot_data.get("searches",{}).get(sid)
    if not d or time.time()-d["created"]>TTL: await q.edit_message_text("❌ Qidiruv eskirgan. Qaytadan qidiring."); return
    await q.edit_message_reply_markup(reply_markup=keyboard(sid,int(p),d["results"]))

async def search_song_cb(update,context):
    q=update.callback_query; await q.answer("⏳ Yuklanmoqda..."); _,sid,idx=q.data.split("|",2); d=context.bot_data.get("searches",{}).get(sid)
    if not d: await q.message.reply_text("❌ Qidiruv eskirgan."); return
    x=d["results"][int(idx)]; await song_download(update,x["url"],x["title"],x.get("channel"))

async def song_download(update,url,title=None,artist=None):
    work=Path(tempfile.mkdtemp(prefix="song_",dir=ROOT))
    try:
        status=await update.callback_query.message.reply_text("⏳ Yuklanmoqda...") if update.callback_query else await update.message.reply_text("⏳ Yuklanmoqda...")
        try:
            info=await asyncio.to_thread(ytget,url,work,True); src=downloaded(work,{".m4a",".webm",".opus",".mp4",".mkv",".aac",".wav"})
            if not src: raise RuntimeError("Audio fayl topilmadi.")
            title=title or info.get("title") or "Noma'lum qo'shiq"; artist=artist or info.get("artist") or info.get("uploader") or ""
            out=work/"song.mp3"; await asyncio.to_thread(mp3,src,out,title,artist)
            with out.open("rb") as f: await (update.callback_query.message if update.callback_query else update.message).reply_audio(audio=f,title=title,performer=artist or None,caption=CAPTION)
            await status.delete()
        except Exception as e: await status.edit_text("❌ MP3 yuklashda xatolik:\n"+err(e))
    finally: clean(work)

async def media_cb(update,context):
    q=update.callback_query; await q.answer(); action,url=q.data.split("|",1); work=Path(tempfile.mkdtemp(prefix="yt_",dir=ROOT))
    try:
        status=await q.message.reply_text("⏳ Yuklanmoqda...")
        try:
            info=await asyncio.to_thread(ytget,url,work,action=="ytmp3"); src=downloaded(work,{".mp4",".mkv",".webm",".mov",".m4v",".m4a",".opus",".aac"})
            if not src: raise RuntimeError("Yuklangan fayl topilmadi.")
            if action=="ytmp3":
                out=work/"song.mp3"; title=info.get("title") or "Noma'lum qo'shiq"; artist=info.get("artist") or info.get("uploader") or ""; await asyncio.to_thread(mp3,src,out,title,artist)
                with out.open("rb") as f: await q.message.reply_audio(audio=f,title=title,performer=artist or None,caption=CAPTION)
            else:
                out=work/"video.mp4"; await asyncio.to_thread(video,src,out)
                with out.open("rb") as f: await q.message.reply_video(video=f,caption=CAPTION,supports_streaming=True)
            await status.delete()
        except Exception as e: await status.edit_text("❌ Yuklashda xatolik:\n"+err(e))
    finally: clean(work)

async def process_url(update,url):
    work=Path(tempfile.mkdtemp(prefix="media_",dir=ROOT)); status=await update.message.reply_text("⏳ Yuklanmoqda...")
    try:
        try:
            await asyncio.to_thread(ytget,url,work,False); src=downloaded(work,{".mp4",".mkv",".webm",".mov",".m4v",".avi"})
            if not src: raise RuntimeError("Video fayl topilmadi.")
            out=work/"video.mp4"; await asyncio.to_thread(video,src,out)
            with out.open("rb") as f: await update.message.reply_video(video=f,caption=CAPTION,supports_streaming=True)
            await status.delete()
        except Exception as e: await status.edit_text("❌ Videoni yuklashda xatolik:\n"+err(e))
    finally: clean(work)

async def recognize(update,context):
    msg=update.message; work=Path(tempfile.mkdtemp(prefix="shazam_",dir=ROOT)); status=await msg.reply_text("⏳ Yuklanmoqda...")
    try:
        try:
            if msg.voice: f=await context.bot.get_file(msg.voice.file_id); ext=".ogg"
            elif msg.audio: f=await context.bot.get_file(msg.audio.file_id); ext=".mp3"
            elif msg.video: f=await context.bot.get_file(msg.video.file_id); ext=".mp4"
            else: f=await context.bot.get_file(msg.video_note.file_id); ext=".mp4"
            src=work/("input"+ext); await f.download_to_drive(custom_path=str(src)); sample=work/"sample.mp3"
            await asyncio.to_thread(ff,["-i",str(src),"-t","90","-vn","-ac","1","-ar","44100","-c:a","libmp3lame","-b:a","128k",str(sample)],180)
            r=await Shazam().recognize(str(sample)); tr=r.get("track") or {}; title=tr.get("title"); artist=tr.get("subtitle") or ""
            if not title: await status.edit_text("❌ Qo'shiq aniqlanmadi."); return
            jid=uuid.uuid4().hex[:10]; context.bot_data.setdefault("jobs",{})[jid]={"title":title,"artist":artist,"created":time.time()}
            await status.edit_text(f"🎵 {artist+' — ' if artist else ''}{title}",reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🎶 To'liq qo'shiq",callback_data=f"fullsong|{jid}")]]))
        except Exception as e: await status.edit_text("❌ Aniqlashda xatolik:\n"+err(e))
    finally: clean(work)

async def fullsong(update,context):
    q=update.callback_query; await q.answer("⏳ Yuklanmoqda..."); _,jid=q.data.split("|",1); j=context.bot_data.get("jobs",{}).get(jid)
    if not j or time.time()-j["created"]>TTL: await q.message.reply_text("❌ Natija eskirgan. Qaytadan audio/video yuboring."); return
    try:
        r=await asyncio.to_thread(search,f'{j["artist"]} {j["title"]}'.strip(),5)
        if not r: await q.message.reply_text("❌ Qo'shiq topilmadi."); return
        await song_download(update,r[0]["url"],j["title"],j["artist"])
    except Exception as e: await q.message.reply_text("❌ Xatolik:\n"+err(e))

async def cleanup(context):
    now=time.time()
    for key in list(context.bot_data.get("searches",{})):
        if now-context.bot_data["searches"][key]["created"]>TTL: context.bot_data["searches"].pop(key,None)
    for key in list(context.bot_data.get("jobs",{})):
        if now-context.bot_data["jobs"][key]["created"]>TTL: context.bot_data["jobs"].pop(key,None)

def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN Railway Variables ichida topilmadi.")
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CallbackQueryHandler(media_cb,pattern=r"^(ytvideo|ytmp3)\|"))
    app.add_handler(CallbackQueryHandler(song_page,pattern=r"^songpage\|"))
    app.add_handler(CallbackQueryHandler(search_song_cb,pattern=r"^searchsong\|"))
    app.add_handler(CallbackQueryHandler(fullsong,pattern=r"^fullsong\|"))
    app.add_handler(MessageHandler(filters.VOICE|filters.AUDIO|filters.VIDEO|filters.VIDEO_NOTE,recognize))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND,text_message))
    if app.job_queue: app.job_queue.run_repeating(cleanup,interval=600,first=600)
    log.info("Bot ishga tushdi."); app.run_polling(allowed_updates=Update.ALL_TYPES,drop_pending_updates=True)

if __name__=="__main__": main()
