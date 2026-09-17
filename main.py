import os, re, asyncio, subprocess, logging
from pathlib import Path
import yt_dlp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, filters

BOT_TOKEN=os.getenv("BOT_TOKEN")
ROOT=Path("/tmp/bot_downloads"); ROOT.mkdir(parents=True,exist_ok=True)
LIMIT=14*1024*1024
MAX_SOURCE=500*1024*1024
COOKIES="/app/cookies.txt"
logging.basicConfig(level=logging.INFO,format="%(asctime)s - %(levelname)s - %(message)s")

def supported(u):
    return any(x in u.lower() for x in ("youtube.com","youtu.be","instagram.com","tiktok.com","facebook.com","fb.watch","ok.ru","odnoklassniki.ru","pinterest.com","pin.it","snapchat.com","likee.video","likee.com","threads.net"))
def youtube(u): return "youtube.com" in u.lower() or "youtu.be" in u.lower()
def baseopts():
    o={"noplaylist":True,"quiet":True,"retries":3,"fragment_retries":3,"socket_timeout":30,"http_chunk_size":10*1024*1024,"concurrent_fragment_downloads":8,"restrictfilenames":True,"max_filesize":MAX_SOURCE}
    if os.path.isfile(COOKIES): o["cookiefile"]=COOKIES
    return o
def newest(d,exts):
    fs=[p for e in exts for p in d.glob("*."+e)]
    fs.sort(key=lambda p:p.stat().st_mtime,reverse=True)
    return str(fs[0]) if fs else None
def duration(f):
    r=subprocess.run(["ffprobe","-v","error","-show_entries","format=duration","-of","default=noprint_wrappers=1:nokey=1",f],capture_output=True,text=True)
    try:return float(r.stdout.strip())
    except:return 0
def width(f):
    r=subprocess.run(["ffprobe","-v","error","-select_streams","v:0","-show_entries","stream=width","-of","csv=p=0",f],capture_output=True,text=True)
    try:return int(r.stdout.strip())
    except:return 480

def video_download(u,d):
    o=baseopts(); o.update({"format":"bestvideo[height<=480]+bestaudio/best[height<=480]/bestvideo+bestaudio/best","outtmpl":str(d/"%(title).80s_%(id)s.%(ext)s"),"merge_output_format":"mp4"})
    with yt_dlp.YoutubeDL(o) as y:
        info=y.extract_info(u,download=True)
    f=newest(d,["mp4","mkv","webm","mov"])
    if not f: raise RuntimeError("Video yuklanmadi.")
    return f,info

def mp3_download(u,d):
    o=baseopts(); o.update({"format":"bestaudio/best","outtmpl":str(d/"%(title).100s_%(id)s.%(ext)s"),"postprocessors":[{"key":"FFmpegExtractAudio","preferredcodec":"mp3","preferredquality":"128"}]})
    with yt_dlp.YoutubeDL(o) as y: info=y.extract_info(u,download=True)
    f=newest(d,["mp3"])
    if not f: raise RuntimeError("MP3 yuklanmadi.")
    return f,info

def encode(src,out,w,vb,ab):
    w=min(w,width(src)); w=max(2,(w//2)*2)
    cmd=["ffmpeg","-y","-i",src,"-vf",f"scale={w}:-2","-c:v","libx264","-preset","ultrafast","-profile:v","main","-pix_fmt","yuv420p","-b:v",f"{vb}k","-maxrate",f"{vb}k","-bufsize",f"{vb*2}k","-c:a","aac","-b:a",f"{ab}k","-ac","2","-ar","44100","-movflags","+faststart",out]
    r=subprocess.run(cmd,capture_output=True,text=True,timeout=900)
    if r.returncode: raise RuntimeError("FFmpeg xatosi: "+r.stderr[-1000:])

def compress(src):
    # 14 MB dan kichik bo'lsa ham qayta kodlanadi; maksimal 480p, upscale yo'q.
    dur=duration(src)
    if dur<=0: raise RuntimeError("Video davomiyligi aniqlanmadi.")
    mw=min(480,width(src)); total=max(70,int(LIMIT*8*0.86/dur/1000))
    candidates=[]
    for w in [480,426,360,320]:
        if w>mw: continue
        a=48 if w>=426 else 32
        v=max(40,total-a)
        for mult in (1,.82,.65,.50): candidates.append((w,max(40,int(v*mult)),a))
    for w,v,a in candidates+[(320,65,32),(320,50,32)]:
        out=str(ROOT/f"enc_{os.getpid()}_{w}_{v}.mp4")
        try:
            encode(src,out,w,v,a)
            if Path(out).exists() and Path(out).stat().st_size<=LIMIT:return out
            Path(out).unlink(missing_ok=True)
        except Exception: Path(out).unlink(missing_ok=True)
    raise RuntimeError("Videoni 14 MB dan kichik qilishning iloji bo'lmadi.")

def clean(d):
    for p in d.glob("*"):
        try:
            if p.is_file():p.unlink()
        except:pass

async def start(u,c):
    await u.message.reply_text("""👋 Assalomu aleykum

📥 MEDIA YUKLASH
• YouTube — video, Shorts, audio
• Instagram — post, reels
• TikTok — video
• Facebook — reels, video
• OK.ru — video
• Pinterest — rasm, video
• Snapchat — rasm, video
• Likee — rasm, video
• Threads — rasm, video

🎬 Video: 480p gacha, 14 MB dan oshmaydi
🎵 MP3: 128 kbps

🚀 Havola yuboring.
🔎 Qo'shiq qidirish uchun qo'shiq nomi yoki ijrochini yozing.""")
async def url_handler(u,c):
    url=u.message.text.strip()
    if not supported(url):
        return await u.message.reply_text("❌ Bu sayt qo'llab-quvvatlanmaydi.")
    if youtube(url):
        kb=InlineKeyboardMarkup([[InlineKeyboardButton("🎬 VIDEO 480p",callback_data="v"),InlineKeyboardButton("🎵 MP3 128 kbps",callback_data="a")]])
        c.user_data["url"]=url
        return await u.message.reply_text("YouTube havolasi qabul qilindi. Formatni tanlang:",reply_markup=kb)
    await do_video(u,c,url)

async def do_video(u,c,url,status_message=None):
    msg=status_message or await u.message.reply_text("⏳ Yuklanmoqda...")
    d=ROOT/str(u.effective_user.id); d.mkdir(exist_ok=True)
    try:
        f,_=await asyncio.to_thread(video_download,url,d)
        
        f=await asyncio.to_thread(compress,f)
        
        with open(f,"rb") as x: await u.message.reply_video(x,supports_streaming=True,read_timeout=180,write_timeout=180)
        await msg.delete()
    except Exception as e:
        await msg.edit_text("❌ Xatolik:\n"+str(e)[-1800:])
    finally: await asyncio.to_thread(clean,d)

async def do_audio(u,c,url,status_message=None):
    msg=status_message or await u.message.reply_text("⏳ Yuklanmoqda...")
    d=ROOT/str(u.effective_user.id); d.mkdir(exist_ok=True)
    try:
        f,info=await asyncio.to_thread(mp3_download,url,d)
        with open(f,"rb") as x: await u.message.reply_audio(x,title=(info.get("title") or "Audio")[:64],performer=(info.get("uploader") or "")[:64],read_timeout=180,write_timeout=180)
        await msg.delete()
    except Exception as e: await msg.edit_text("❌ MP3 xatolik:\n"+str(e)[-1800:])
    finally: await asyncio.to_thread(clean,d)

def search50(q):
    o={
        "quiet":True,
        "no_warnings":True,
        "extract_flat":True,
        "playlistend":50,
        "default_search":"ytsearch50",
        "noplaylist":False,
    }
    if os.path.isfile(COOKIES): o["cookiefile"]=COOKIES
    with yt_dlp.YoutubeDL(o) as y:
        result=y.extract_info("ytsearch50:"+q,download=False)
    return [e for e in (result.get("entries") or []) if e][:50]

def search_keyboard(results,page):
    start=page*10
    end=min(start+10,len(results))
    rows=[]
    for i in range(start,end):
        title=(results[i].get("title") or "Noma'lum")[:45]
        if len(results[i].get("title") or "")>45: title=title[:42]+"..."
        rows.append([InlineKeyboardButton(f"{i+1}. {title}",callback_data=f"s|{i+1}")])
    nav=[]
    if page>0: nav.append(InlineKeyboardButton("⬅️ Oldingi",callback_data=f"p|{page-1}"))
    if end<len(results): nav.append(InlineKeyboardButton("Keyingi ➡️",callback_data=f"p|{page+1}"))
    if nav: rows.append(nav)
    return InlineKeyboardMarkup(rows)

async def search(u,c):
    msg=await u.message.reply_text("⏳ Yuklanmoqda...")
    try:
        rs=await asyncio.to_thread(search50,u.message.text.strip())
        if not rs:
            return await msg.edit_text("❌ Qo'shiq topilmadi.")
        c.user_data["results"]=[e.get("webpage_url") or "https://www.youtube.com/watch?v="+e["id"] for e in rs]
        c.user_data["result_titles"]=[e.get("title") or "Noma'lum" for e in rs]
        c.user_data["page"]=0
        await msg.edit_text("🎵 50 ta natija — 1/5-sahifa",reply_markup=search_keyboard(rs,0))
    except Exception as e:
        await msg.edit_text("❌ Qidiruv xatosi:\n"+str(e)[-1800:])

async def buttons(u,c):
    q=u.callback_query
    await q.answer()
    data=q.data
    if data=="v":
        return await do_video(q,c,c.user_data.get("url"))
    if data=="a":
        return await do_audio(q,c,c.user_data.get("url"))
    if data.startswith("p|"):
        try: page=int(data.split("|")[1])
        except: return
        urls=c.user_data.get("results",[])
        titles=c.user_data.get("result_titles",[])
        if not urls: return await q.message.reply_text("❌ Qidiruv eskirgan. Qaytadan qidiring.")
        entries=[{"url":urls[i],"title":titles[i] if i<len(titles) else "Noma'lum"} for i in range(len(urls))]
        c.user_data["page"]=page
        total=(len(entries)+9)//10
        await q.edit_message_text(f"🎵 50 ta natija — {page+1}/{total}-sahifa",reply_markup=search_keyboard(entries,page))
        return
    if data.startswith("s|"):
        try: number=int(data.split("|")[1])
        except: return
        urls=c.user_data.get("results",[])
        if number<1 or number>len(urls):
            return await q.message.reply_text("❌ Natija eskirgan. Qaytadan qidiring.")
        await q.edit_message_text("⏳ Yuklanmoqda...")
        return await do_audio(q,c,urls[number-1],status_message=q.message)

async def text(u,c):
    t=u.message.text.strip()
    if re.match(r"^https?://",t,re.I): await url_handler(u,c)
    else: await search(u,c)

def main():
    if not BOT_TOKEN: raise RuntimeError("BOT_TOKEN Railway Variables ichida yo'q!")
    app=Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start",start))
    app.add_handler(CallbackQueryHandler(buttons))
    app.add_handler(MessageHandler(filters.TEXT&~filters.COMMAND,text))
    app.run_polling(drop_pending_updates=True)
if __name__=="__main__":main()
