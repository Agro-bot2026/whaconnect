#!/usr/bin/env bash
set -e
TOKEN="__TOKEN__"
API_BASE="__API_BASE__"
BOT_DIR="/opt/whaconnect-bot"

echo "==> Validando licencia..."
RESP=$(curl -s "$API_BASE/api/validate/$TOKEN")
echo "$RESP" | grep -q '"ok":true' || { echo "❌ Licencia/IP no autorizada: $RESP"; exit 1; }
echo "✅ Licencia validada."

echo "==> Instalando dependencias base del sistema..."
apt-get update -y
apt-get install -y curl git build-essential ca-certificates python3 >/dev/null 2>&1 || true
command -v node >/dev/null 2>&1 || { curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs; }
command -v pm2  >/dev/null 2>&1 || npm install -g pm2

mkdir -p "$BOT_DIR" && cd "$BOT_DIR"
echo "$TOKEN"    > .license
echo "$API_BASE" > .apibase
mkdir -p archivos

cat > package.json <<'PKG'
{ "name":"whaconnect-bot","version":"1.0.0","main":"index.js",
  "dependencies":{"@whiskeysockets/baileys":"^6.7.9","@hapi/boom":"^10.0.1","qrcode-terminal":"^0.12.0","pino":"^9.5.0","axios":"^1.7.0"} }
PKG

npm install --no-audit --no-fund

cat > guard.js <<'GUARD'
const fs=require('fs'),axios=require('axios');
const token=fs.readFileSync(__dirname+'/.license','utf8').trim();
const api=fs.readFileSync(__dirname+'/.apibase','utf8').trim();
const BOT_VERSION="1.0.0";
module.exports=async function guard(){
  try{const r=await axios.get(`${api}/api/validate/${token}?v=${BOT_VERSION}`,{timeout:10000});
    if(!r.data.ok){console.error('❌ Licencia no válida:',r.data.reason);process.exit(1);}
  }catch(e){console.error('❌ No se pudo validar la licencia:',e.message);process.exit(1);}
};
GUARD

cat > index.js <<'JS'
const fs=require('fs'), path=require('path'), axios=require('axios');
const guard=require('./guard');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const qrcode=require('qrcode-terminal'); const { Boom }=require('@hapi/boom'); const pino=require('pino');

const TOKEN=fs.readFileSync(__dirname+'/.license','utf8').trim();
const API=fs.readFileSync(__dirname+'/.apibase','utf8').trim();
const ARCH=path.join(__dirname,'archivos');
const CMDARCH=path.join(__dirname,'cmd_archivos');
if(!fs.existsSync(CMDARCH)) fs.mkdirSync(CMDARCH,{recursive:true});
if(!fs.existsSync(ARCH)) fs.mkdirSync(ARCH,{recursive:true});

let COMMANDS=[], FILES=[];
let CFG={welcome_on:false,welcome_text:'',welcome_photo:true,farewell_on:false,farewell_text:'',
  antilink_on:false,antilink_action:'borrar',antispam_on:false,antispam_max:5,antispam_seconds:10,antispam_action:'borrar'};

const LINK_RE=/(https?:\/\/|www\.|chat\.whatsapp\.com|t\.me\/|\b[\w-]+\.(com|net|org|io|me|info|xyz|link|app)\b)/i;
const spamMap={}; // jid -> [timestamps]

async function syncCommands(){ try{ const r=await axios.get(`${API}/api/bot/commands/${TOKEN}`,{timeout:10000}); COMMANDS=r.data||[]; }catch(e){} }
async function syncSettings(){ try{ const r=await axios.get(`${API}/api/bot/settings/${TOKEN}`,{timeout:10000}); CFG=r.data||CFG; }catch(e){} }
async function syncFiles(){
  try{
    const r=await axios.get(`${API}/api/bot/files/${TOKEN}`,{timeout:10000}); FILES=r.data||[];
    for(const f of FILES){ const fp=path.join(ARCH,f.filename);
      if(!fs.existsSync(fp)){ try{ const dl=await axios.get(`${API}/api/bot/files/${TOKEN}/${f.id}`,{responseType:'arraybuffer',timeout:30000}); fs.writeFileSync(fp,Buffer.from(dl.data)); }catch(e){} } }
    const validos=new Set(FILES.map(f=>f.filename));
    for(const local of fs.readdirSync(ARCH)){ if(!validos.has(local)){ try{ fs.unlinkSync(path.join(ARCH,local)); }catch(e){} } }
  }catch(e){}
}
function findCmd(text){ const t=text.trim().toLowerCase(); return COMMANDS.find(c=>c.trigger.trim().toLowerCase()===t); }

async function isAdmin(sock,group,jid){
  try{ const meta=await sock.groupMetadata(group); const p=meta.participants.find(x=>x.id===jid); return p && (p.admin==='admin'||p.admin==='superadmin'); }catch{ return false; }
}

function mimeDe(nombre){
  const e=nombre.split('.').pop().toLowerCase();
  const m={jpg:'image/jpeg',jpeg:'image/jpeg',png:'image/png',gif:'image/gif',webp:'image/webp',
    mp4:'video/mp4',mkv:'video/x-matroska',mov:'video/quicktime',
    mp3:'audio/mpeg',ogg:'audio/ogg',m4a:'audio/mp4',wav:'audio/wav',
    pdf:'application/pdf',zip:'application/zip',rar:'application/x-rar-compressed',
    doc:'application/msword',docx:'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    xls:'application/vnd.ms-excel',xlsx:'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    txt:'text/plain',apk:'application/vnd.android.package-archive'};
  return m[e]||'application/octet-stream';
}
async function enviarArchivo(sock,to,fp,nombre,caption){
  const e=nombre.split('.').pop().toLowerCase();
  const mt=mimeDe(nombre);
  const cap=caption||'';
  if(['jpg','jpeg','png','gif','webp'].includes(e)) return sock.sendMessage(to,{image:{url:fp},caption:cap});
  if(['mp4','mov','mkv'].includes(e)) return sock.sendMessage(to,{video:{url:fp},caption:cap});
  if(['mp3','ogg','m4a','wav'].includes(e)) return sock.sendMessage(to,{audio:{url:fp},mimetype:mt});
  return sock.sendMessage(to,{document:{url:fp},fileName:nombre,mimetype:mt,caption:cap});
}


async function syncCommandFiles(){
  try{
    const r=await axios.get(`${API}/api/bot/command-files/${TOKEN}`,{timeout:15000});
    for(const f of r.data){
      const fp=path.join(CMDARCH,f.filename);
      if(!fs.existsSync(fp)){
        const resp=await axios.get(`${API}/api/bot/command-file/${TOKEN}/${encodeURIComponent(f.filename)}`,{responseType:'arraybuffer',timeout:30000});
        fs.writeFileSync(fp,Buffer.from(resp.data));
        console.log('Archivo de comando descargado:',f.filename);
      }
    }
  }catch(e){}
}

async function start(){
  await guard(); await syncCommands(); await syncSettings(); await syncFiles();
  const { state, saveCreds } = await useMultiFileAuthState('auth');
  const { version } = await fetchLatestBaileysVersion();
  const sock = makeWASocket({ version, auth: state, printQRInTerminal:false, logger: pino({level:'silent'}) });
  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (u)=>{
    const { connection, lastDisconnect, qr } = u;
    if(qr){ console.log('\n📱 Escaneá este QR (WhatsApp > Dispositivos vinculados):\n'); qrcode.generate(qr,{small:true}); }
    if(connection==='close'){
      const out=(lastDisconnect?.error instanceof Boom)?lastDisconnect.error.output.statusCode:0;
      if(out===DisconnectReason.loggedOut){ console.log('Sesión cerrada.'); process.exit(0); } else start();
    } else if(connection==='open'){ console.log('\n✅ Bot conectado.\n'); }
  });

  sock.ev.on('group-participants.update', async (ev)=>{
    try{
      const meta=await sock.groupMetadata(ev.id); const groupName=meta.subject||'el grupo';
      for(const jid of ev.participants){
        const mention='@'+jid.split('@')[0];
        if(ev.action==='add' && CFG.welcome_on){
          let texto=(CFG.welcome_text||'¡Bienvenido {usuario} a {grupo}!').replace(/{usuario}/g,mention).replace(/{grupo}/g,groupName);
          if(CFG.welcome_photo){ let f=null; try{ f=await sock.profilePictureUrl(jid,'image'); }catch{ f=null; } if(f){ await sock.sendMessage(ev.id,{ image:{url:f}, caption:texto, mentions:[jid] }); continue; } }
          await sock.sendMessage(ev.id,{ text:texto, mentions:[jid] });
        } else if(ev.action==='remove' && CFG.farewell_on){
          let texto=(CFG.farewell_text||'👋 {usuario} salió de {grupo}').replace(/{usuario}/g,mention).replace(/{grupo}/g,groupName);
          await sock.sendMessage(ev.id,{ text:texto, mentions:[jid] });
        }
      }
    }catch(e){ console.error('bienvenida:',e.message); }
  });

  sock.ev.on('messages.upsert', async ({messages,type})=>{
    if(type!=='notify')return; const m=messages[0]; if(!m.message||m.key.fromMe)return;
    const from=m.key.remoteJid; const isGroup=from.endsWith('@g.us');
    const sender=m.key.participant||from;
    const txt=(m.message.conversation||m.message.extendedTextMessage?.text||'').trim();

    // ---- ANTILINK ----
    if(isGroup && CFG.antilink_on && txt && LINK_RE.test(txt)){
      if(!(await isAdmin(sock,from,sender))){
        try{
          await sock.sendMessage(from,{ delete:m.key });
          if(CFG.antilink_action==='advertir'){
            await sock.sendMessage(from,{ text:'⚠️ No se permiten enlaces. @'+sender.split('@')[0], mentions:[sender] });
          } else if(CFG.antilink_action==='expulsar'){
            await sock.groupParticipantsUpdate(from,[sender],'remove');
          }
        }catch(e){ console.error('antilink:',e.message); }
        return;
      }
    }

    // ---- ANTISPAM ----
    if(isGroup && CFG.antispam_on && txt){
      if(!(await isAdmin(sock,from,sender))){
        const now=Date.now(); const win=(CFG.antispam_seconds||10)*1000;
        spamMap[sender]=(spamMap[sender]||[]).filter(t=>now-t<win); spamMap[sender].push(now);
        if(spamMap[sender].length>(CFG.antispam_max||5)){
          try{
            if(CFG.antispam_action==='advertir'){
              await sock.sendMessage(from,{ text:'⚠️ Estás enviando demasiados mensajes. @'+sender.split('@')[0], mentions:[sender] });
            } else {
              await sock.sendMessage(from,{ delete:m.key });
            }
          }catch(e){ console.error('antispam:',e.message); }
          spamMap[sender]=[];
          return;
        }
      }
    }

    if(!txt) return; const low=txt.toLowerCase();

    if(low==='/archivos' || low==='/listar archivos' || low==='listar archivos'){
      if(FILES.length===0) return sock.sendMessage(from,{text:'No hay archivos disponibles.'});
      if(low!=='/archivos'){ const lista=FILES.map((f,i)=>`${i+1}. ${f.filename}`).join('\n'); return sock.sendMessage(from,{text:'📁 Archivos disponibles:\n'+lista+'\n\nEscribí /archivos para recibirlos.'}); }
      for(const f of FILES){ const fp=path.join(ARCH,f.filename); if(fs.existsSync(fp)) await enviarArchivo(sock,from,fp,f.filename); }
      return;
    }

    // ===== IA del bot (soporte) =====
    // En grupos: responde si el usuario cita/responde un mensaje del bot.
    // En privado: responde directo.
    const ctxInfo = m.message.extendedTextMessage?.contextInfo
                 || m.message.imageMessage?.contextInfo
                 || m.message.videoMessage?.contextInfo
                 || m.message.audioMessage?.contextInfo;
    const miId = (sock.user && sock.user.id ? sock.user.id : '');
    const miNum = miId.replace(/[:@].*$/, '').replace(/\D/g,'');
    const miLid = (sock.user && sock.user.lid ? sock.user.lid : '').replace(/[:@].*$/, '').replace(/\D/g,'');
    const part = (ctxInfo && ctxInfo.participant ? ctxInfo.participant : '');
    const partNum = part.replace(/[:@].*$/, '').replace(/\D/g,'');
    const citaAlBot = !!part && ( (miNum && partNum.includes(miNum)) || (miLid && partNum.includes(miLid)) );
    const esRespuestaABot = ctxInfo && ctxInfo.quotedMessage && citaAlBot;
    if(txt && !findCmd(txt)){
      const debeResponderIA = (!isGroup) || esRespuestaABot;
      if(debeResponderIA){
        try{
          // Mostrar "grabando audio..." mientras se genera la respuesta
          try { await sock.sendPresenceUpdate('recording', from); } catch(e){}
          const r = await axios.post(`${API}/api/bot/ai/${TOKEN}`, { pregunta: txt }, { timeout: 90000 });
          if(r.data && r.data.respuesta){
            if(r.data.audio){
              const audioBuf = Buffer.from(r.data.audio, 'base64');
              await sock.sendMessage(from, { audio: audioBuf, mimetype: 'audio/ogg; codecs=opus', ptt: true }, { quoted: m });
              try { await sock.sendPresenceUpdate('paused', from); } catch(e){}
            } else {
              await sock.sendMessage(from, { text: r.data.respuesta }, { quoted: m });
            }
            return;
          }
        }catch(e){
          // si la IA no esta activa (403) o falla, seguimos sin responder con IA
        }
      }
    }

    const c=findCmd(txt); if(!c) return;
    const ctx=m.message.extendedTextMessage?.contextInfo;
    const targetJid = ctx?.participant || (ctx?.mentionedJid && ctx.mentionedJid[0]);
    try{
      if(c.type==='response'){
        if(c.image_file){
          const ip=path.join(CMDARCH,c.image_file);
          if(fs.existsSync(ip)){ await enviarArchivo(sock,from,ip,c.image_file,c.content||''); return; }
        }
        await sock.sendMessage(from,{ text:c.content||'' }); return;
      }
      const a=c.content;
      if(a==='expulsar'){ if(!isGroup||!targetJid) return sock.sendMessage(from,{text:'Respondé al mensaje de quien querés expulsar.'}); await sock.groupParticipantsUpdate(from,[targetJid],'remove'); }
      else if(a==='promover'){ if(!isGroup||!targetJid) return sock.sendMessage(from,{text:'Respondé al mensaje de quien querés promover.'}); await sock.groupParticipantsUpdate(from,[targetJid],'promote'); }
      else if(a==='silenciar'){ if(!isGroup) return; const meta=await sock.groupMetadata(from); const nuevo=meta.announce?'not_announcement':'announcement'; await sock.groupSettingUpdate(from,nuevo); await sock.sendMessage(from,{text:nuevo==='announcement'?'🔇 Grupo silenciado.':'🔊 Grupo abierto.'}); }
      else if(a==='todos'){ if(!isGroup) return; const meta=await sock.groupMetadata(from); const jids=meta.participants.map(p=>p.id); await sock.sendMessage(from,{ text:jids.map(j=>'@'+j.split('@')[0]).join(' '), mentions:jids }); }
      else if(a==='warn'){ if(!isGroup||!targetJid) return sock.sendMessage(from,{text:'Respondé al mensaje de quien querés advertir.'}); await sock.sendMessage(from,{ text:'⚠️ Advertencia para @'+targetJid.split('@')[0], mentions:[targetJid] }); }
      else if(a==='enviar_archivo'){ if(FILES.length===0) return sock.sendMessage(from,{text:'No hay archivos guardados.'}); const f=FILES[0]; const fp=path.join(ARCH,f.filename); if(fs.existsSync(fp)) await enviarArchivo(sock,from,fp,f.filename); }
      else if(a==='borrar'){ if(!isGroup||!ctx) return sock.sendMessage(from,{text:'Respondé al mensaje que querés borrar.'}); await sock.sendMessage(from,{ delete:{ remoteJid:from, fromMe:false, id:ctx.stanzaId, participant:ctx.participant } }); }
    }catch(e){ console.error('acción',c.content,':',e.message); await sock.sendMessage(from,{ text:'No pude ejecutar la acción. ¿Soy admin del grupo?' }); }
  });

  setInterval(guard, 6*60*60*1000);
  setInterval(syncCommands, 30*1000);
  setInterval(syncSettings, 30*1000);
  setInterval(syncFiles, 60*1000);
  syncCommandFiles(); setInterval(syncCommandFiles, 60*1000);
}
start();
JS

cat > /usr/local/bin/whabot <<'WB'
#!/usr/bin/env bash
BOT_DIR="/opt/whaconnect-bot"
cd "$BOT_DIR" || { echo "Bot no instalado."; exit 1; }
if [ "$1" = "reset" ]; then rm -rf "$BOT_DIR/auth"; echo "🔄 Sesión borrada, se generará un QR nuevo."; fi
if [ "$1" = "update" ]; then
  TOK=$(cat "$BOT_DIR/.license"); APIB=$(cat "$BOT_DIR/.apibase")
  echo "Actualizando a la ultima version..."
  pm2 stop whaconnect-bot 2>/dev/null
  bash <(curl -s "$APIB/install/$TOK")
  pm2 start "$BOT_DIR/index.js" --name whaconnect-bot 2>/dev/null && pm2 save
  echo "Actualizado. Tu sesion de WhatsApp se mantiene."
  exit 0
fi
if [ "$1" = "fondo" ]; then pm2 start index.js --name whaconnect-bot && pm2 save; exit 0; fi
if [ "$1" = "stop" ]; then pm2 stop whaconnect-bot; exit 0; fi
if [ "$1" = "logs" ]; then pm2 logs whaconnect-bot; exit 0; fi
exec node index.js
WB
chmod +x /usr/local/bin/whabot

echo ""
echo "============================================"
echo " ✅ Bot instalado en $BOT_DIR"
echo " Vincular WhatsApp (QR):  whabot"
echo " QR nuevo:                whabot reset"
echo " Segundo plano:           whabot fondo"
echo " Detener:                 whabot stop"
echo " Logs:                    whabot logs"
echo "============================================"
