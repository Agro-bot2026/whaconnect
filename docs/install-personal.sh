#!/usr/bin/env bash
# ============================================================
#  INSTALADOR PERSONAL (USO PRIVADO) - SIN LICENCIA NI IP
#  Bot de WhatsApp autónomo. Comandos en comandos.json local.
#  NO compartir este script.
# ============================================================
set -e
BOT_DIR="/opt/whabot-personal"

echo "==> Instalando dependencias base..."
apt-get update -y
apt-get install -y curl git build-essential ca-certificates >/dev/null 2>&1 || true
command -v node >/dev/null 2>&1 || { curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && apt-get install -y nodejs; }
command -v pm2  >/dev/null 2>&1 || npm install -g pm2

mkdir -p "$BOT_DIR" && cd "$BOT_DIR"
mkdir -p archivos

cat > package.json <<'PKG'
{ "name":"whabot-personal","version":"1.0.0","main":"index.js",
  "dependencies":{"@whiskeysockets/baileys":"^6.7.9","@hapi/boom":"^10.0.1","qrcode-terminal":"^0.12.0","pino":"^9.5.0"} }
PKG

npm install --no-audit --no-fund

# Config local: editá este archivo para tus comandos y acciones automáticas
if [ ! -f comandos.json ]; then
cat > comandos.json <<'CFG'
{
  "comandos": [
    { "trigger": "hola", "type": "response", "content": "¡Hola! Soy el bot 🤖" },
    { "trigger": "/ban", "type": "action", "content": "expulsar" }
  ],
  "settings": {
    "welcome_on": true,
    "welcome_text": "¡Bienvenido {usuario} a {grupo}! 🎉",
    "welcome_photo": true,
    "farewell_on": false,
    "farewell_text": "👋 {usuario} salió de {grupo}",
    "antilink_on": false,
    "antilink_action": "borrar",
    "antispam_on": false,
    "antispam_max": 5,
    "antispam_seconds": 10,
    "antispam_action": "borrar"
  }
}
CFG
fi

cat > index.js <<'JS'
const fs=require('fs'), path=require('path');
const { default: makeWASocket, useMultiFileAuthState, DisconnectReason, fetchLatestBaileysVersion } = require('@whiskeysockets/baileys');
const qrcode=require('qrcode-terminal'); const { Boom }=require('@hapi/boom'); const pino=require('pino');

const CFGFILE=path.join(__dirname,'comandos.json');
const ARCH=path.join(__dirname,'archivos');
if(!fs.existsSync(ARCH)) fs.mkdirSync(ARCH,{recursive:true});

let COMMANDS=[], CFG={};
function loadConfig(){
  try{ const j=JSON.parse(fs.readFileSync(CFGFILE,'utf8')); COMMANDS=j.comandos||[]; CFG=j.settings||{}; }
  catch(e){ console.error('Error leyendo comandos.json:',e.message); }
}
loadConfig();
fs.watchFile(CFGFILE,{interval:3000},()=>{ console.log('🔄 comandos.json actualizado'); loadConfig(); });

const LINK_RE=/(https?:\/\/|www\.|chat\.whatsapp\.com|t\.me\/|\b[\w-]+\.(com|net|org|io|me|info|xyz|link|app)\b)/i;
const spamMap={};
function findCmd(text){ const t=text.trim().toLowerCase(); return COMMANDS.find(c=>c.trigger.trim().toLowerCase()===t); }
async function isAdmin(sock,group,jid){ try{ const meta=await sock.groupMetadata(group); const p=meta.participants.find(x=>x.id===jid); return p&&(p.admin==='admin'||p.admin==='superadmin'); }catch{ return false; } }

async function start(){
  const { state, saveCreds } = await useMultiFileAuthState('auth');
  const { version } = await fetchLatestBaileysVersion();
  const sock = makeWASocket({ version, auth: state, printQRInTerminal:false, logger: pino({level:'silent'}) });
  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update',(u)=>{
    const { connection, lastDisconnect, qr } = u;
    if(qr){ console.log('\n📱 Escaneá este QR (WhatsApp > Dispositivos vinculados):\n'); qrcode.generate(qr,{small:true}); }
    if(connection==='close'){ const out=(lastDisconnect?.error instanceof Boom)?lastDisconnect.error.output.statusCode:0;
      if(out===DisconnectReason.loggedOut){ console.log('Sesión cerrada.'); process.exit(0); } else start(); }
    else if(connection==='open'){ console.log('\n✅ Bot conectado.\n'); }
  });

  sock.ev.on('group-participants.update', async (ev)=>{
    try{ const meta=await sock.groupMetadata(ev.id); const g=meta.subject||'el grupo';
      for(const jid of ev.participants){ const mention='@'+jid.split('@')[0];
        if(ev.action==='add' && CFG.welcome_on){
          let t=(CFG.welcome_text||'¡Bienvenido {usuario} a {grupo}!').replace(/{usuario}/g,mention).replace(/{grupo}/g,g);
          if(CFG.welcome_photo){ let f=null; try{ f=await sock.profilePictureUrl(jid,'image'); }catch{ f=null; } if(f){ await sock.sendMessage(ev.id,{ image:{url:f}, caption:t, mentions:[jid] }); continue; } }
          await sock.sendMessage(ev.id,{ text:t, mentions:[jid] });
        } else if(ev.action==='remove' && CFG.farewell_on){
          let t=(CFG.farewell_text||'👋 {usuario} salió de {grupo}').replace(/{usuario}/g,mention).replace(/{grupo}/g,g);
          await sock.sendMessage(ev.id,{ text:t, mentions:[jid] });
        }
      }
    }catch(e){ console.error('bienvenida:',e.message); }
  });

  sock.ev.on('messages.upsert', async ({messages,type})=>{
    if(type!=='notify')return; const m=messages[0]; if(!m.message||m.key.fromMe)return;
    const from=m.key.remoteJid; const isGroup=from.endsWith('@g.us'); const sender=m.key.participant||from;
    const txt=(m.message.conversation||m.message.extendedTextMessage?.text||'').trim();

    if(isGroup && CFG.antilink_on && txt && LINK_RE.test(txt)){
      if(!(await isAdmin(sock,from,sender))){ try{ await sock.sendMessage(from,{ delete:m.key });
        if(CFG.antilink_action==='advertir') await sock.sendMessage(from,{ text:'⚠️ No se permiten enlaces. @'+sender.split('@')[0], mentions:[sender] });
        else if(CFG.antilink_action==='expulsar') await sock.groupParticipantsUpdate(from,[sender],'remove');
      }catch(e){} return; }
    }
    if(isGroup && CFG.antispam_on && txt){
      if(!(await isAdmin(sock,from,sender))){ const now=Date.now(); const win=(CFG.antispam_seconds||10)*1000;
        spamMap[sender]=(spamMap[sender]||[]).filter(t=>now-t<win); spamMap[sender].push(now);
        if(spamMap[sender].length>(CFG.antispam_max||5)){ try{
          if(CFG.antispam_action==='advertir') await sock.sendMessage(from,{ text:'⚠️ Demasiados mensajes. @'+sender.split('@')[0], mentions:[sender] });
          else await sock.sendMessage(from,{ delete:m.key });
        }catch(e){} spamMap[sender]=[]; return; } }
    }

    if(!txt) return; const low=txt.toLowerCase();
    if(low==='/archivos' || low==='/listar archivos' || low==='listar archivos'){
      const files=fs.existsSync(ARCH)?fs.readdirSync(ARCH):[];
      if(files.length===0) return sock.sendMessage(from,{text:'No hay archivos disponibles.'});
      if(low!=='/archivos'){ return sock.sendMessage(from,{text:'📁 Archivos:\n'+files.map((f,i)=>`${i+1}. ${f}`).join('\n')+'\n\nEscribí /archivos para recibirlos.'}); }
      for(const f of files){ await sock.sendMessage(from,{ document:{url:path.join(ARCH,f)}, fileName:f }); }
      return;
    }

    const c=findCmd(txt); if(!c) return;
    const ctx=m.message.extendedTextMessage?.contextInfo;
    const targetJid = ctx?.participant || (ctx?.mentionedJid && ctx.mentionedJid[0]);
    try{
      if(c.type==='response'){ await sock.sendMessage(from,{ text:c.content||'' }); return; }
      const a=c.content;
      if(a==='expulsar'){ if(!isGroup||!targetJid) return sock.sendMessage(from,{text:'Respondé al mensaje del objetivo.'}); await sock.groupParticipantsUpdate(from,[targetJid],'remove'); }
      else if(a==='promover'){ if(!isGroup||!targetJid) return; await sock.groupParticipantsUpdate(from,[targetJid],'promote'); }
      else if(a==='silenciar'){ if(!isGroup) return; const meta=await sock.groupMetadata(from); const n=meta.announce?'not_announcement':'announcement'; await sock.groupSettingUpdate(from,n); await sock.sendMessage(from,{text:n==='announcement'?'🔇 Silenciado.':'🔊 Abierto.'}); }
      else if(a==='todos'){ if(!isGroup) return; const meta=await sock.groupMetadata(from); const jids=meta.participants.map(p=>p.id); await sock.sendMessage(from,{ text:jids.map(j=>'@'+j.split('@')[0]).join(' '), mentions:jids }); }
      else if(a==='warn'){ if(!isGroup||!targetJid) return; await sock.sendMessage(from,{ text:'⚠️ Advertencia para @'+targetJid.split('@')[0], mentions:[targetJid] }); }
      else if(a==='enviar_archivo'){ const files=fs.readdirSync(ARCH); if(files.length===0) return sock.sendMessage(from,{text:'No hay archivos.'}); await sock.sendMessage(from,{ document:{url:path.join(ARCH,files[0])}, fileName:files[0] }); }
      else if(a==='borrar'){ if(!isGroup||!ctx) return; await sock.sendMessage(from,{ delete:{ remoteJid:from, fromMe:false, id:ctx.stanzaId, participant:ctx.participant } }); }
    }catch(e){ console.error('acción:',e.message); await sock.sendMessage(from,{ text:'No pude ejecutar. ¿Soy admin del grupo?' }); }
  });
}
start();
JS

cat > /usr/local/bin/whabot-personal <<'WB'
#!/usr/bin/env bash
BOT_DIR="/opt/whabot-personal"
cd "$BOT_DIR" || { echo "No instalado."; exit 1; }
if [ "$1" = "reset" ]; then rm -rf "$BOT_DIR/auth"; echo "🔄 Sesión borrada."; fi
if [ "$1" = "fondo" ]; then pm2 start index.js --name whabot-personal && pm2 save; exit 0; fi
if [ "$1" = "stop" ]; then pm2 stop whabot-personal; exit 0; fi
if [ "$1" = "logs" ]; then pm2 logs whabot-personal; exit 0; fi
exec node index.js
WB
chmod +x /usr/local/bin/whabot-personal

echo ""
echo "============================================"
echo " ✅ Bot PERSONAL instalado en $BOT_DIR"
echo " Editá tus comandos:  nano $BOT_DIR/comandos.json"
echo " Vincular WhatsApp:   whabot-personal"
echo " Segundo plano:       whabot-personal fondo"
echo " Logs:                whabot-personal logs"
echo "============================================"
