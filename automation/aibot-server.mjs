import fs from 'node:fs';
import path from 'node:path';
import { execFile } from 'node:child_process';
import { promisify } from 'node:util';
import AiBot, { generateReqId } from '@wecom/aibot-node-sdk';

const execFileAsync = promisify(execFile);

const env = Object.fromEntries(
  fs.readFileSync('/opt/yuxiaor-aibot/aibot.env', 'utf8')
    .split(/\r?\n/)
    .filter(Boolean)
    .map(line => {
      const pos = line.indexOf('=');
      return [line.slice(0, pos), line.slice(pos + 1)];
    })
);

// “数据汇报会员群”的企业微信群聊 ID。
const ALLOWED_CHAT_ID = 'wrki7WEAAAYzG-hYJ4delzv_Y7Us71ow';
const NOTIFICATION_ROOT = '/opt/yuxiaor-automation/notifications';
const PENDING_DIR = path.join(NOTIFICATION_ROOT, 'pending');
const SENT_DIR = path.join(NOTIFICATION_ROOT, 'sent');
const FAILED_DIR = path.join(NOTIFICATION_ROOT, 'failed');

const client = new AiBot.WSClient({
  botId: env.WECOM_BOT_ID,
  secret: env.WECOM_BOT_SECRET,
});

async function countJsonFiles(directory) {
  return (await fs.promises.readdir(directory).catch(() => []))
    .filter(name => name.endsWith('.json')).length;
}

async function unitStatus(unit) {
  try {
    const { stdout } = await execFileAsync('/usr/bin/systemctl', ['is-active', unit]);
    return stdout.trim() === 'active' ? '运行中' : stdout.trim();
  } catch (error) {
    const state = String(error?.stdout || '').trim();
    return state || '异常';
  }
}

async function buildStatusReport(job) {
  const [timer, bot, pending, failed] = await Promise.all([
    unitStatus('yuxiaor-download.timer'),
    unitStatus('yuxiaor-aibot.service'),
    countJsonFiles(PENDING_DIR),
    countJsonFiles(FAILED_DIR),
  ]);
  const serverState = timer === '运行中' && bot === '运行中' ? '服务器状态正常：' : '服务器状态异常：';
  return [
    '**状态监测情况**',
    '',
    serverState,
    `> 每小时定时器：${timer}`,
    `> 企业微信机器人：${bot}`,
    `> 待发送：${pending}`,
    `> 发送失败：${failed}`,
    `> 本次消息及${job.files.length}份文件：发送成功`,
    '',
    job.summary,
  ].join('\n');
}

function normalizeInput(input) {
  return input.trim().replace(/^@习院数据助手\s*/, '').trim();
}

function answer(input) {
  const text = normalizeInput(input);
  if (/^(帮助|help)$/i.test(text)) {
    return '习院数据助手已连接。当前支持：帮助、状态。云端导出与文件发送功能正在接入。';
  }
  if (/^(状态|status)$/i.test(text)) {
    return `云端机器人运行正常。时间：${new Date().toLocaleString('zh-CN', { timeZone: 'Asia/Shanghai' })}`;
  }
  return `已收到：${text}\n当前基础连接已完成，请发送“帮助”查看可用功能。`;
}

client.on('authenticated', () => console.log('WeCom AI Bot authenticated'));
client.on('message.text', async frame => {
  const body = frame.body || {};
  const allowed = body.chattype === 'group' && body.chatid === ALLOWED_CHAT_ID;
  console.log(JSON.stringify({
    at: new Date().toISOString(),
    type: 'text',
    chattype: body.chattype,
    chatid: body.chatid,
    userid: body.from?.userid,
    allowed,
  }));
  if (!allowed) return;

  const content = body.text?.content || '';
  const streamId = generateReqId('xiyuan');
  await client.replyStream(frame, streamId, answer(content), true);
});

// 此机器人只用于指定的内部群，不处理个人单聊进入事件。
client.on('event.enter_chat', frame => {
  const body = frame.body || {};
  console.log(JSON.stringify({
    at: new Date().toISOString(),
    type: 'enter_chat',
    chattype: body.chattype,
    chatid: body.chatid,
    userid: body.from?.userid,
    allowed: body.chattype === 'group' && body.chatid === ALLOWED_CHAT_ID,
  }));
});

client.on('error', error => console.error('WeCom AI Bot error', error));
client.connect();

let queueBusy = false;
async function processNotificationQueue() {
  if (queueBusy || !client.isConnected) return;
  queueBusy = true;
  try {
    await fs.promises.mkdir(PENDING_DIR, { recursive: true });
    await fs.promises.mkdir(SENT_DIR, { recursive: true });
    await fs.promises.mkdir(FAILED_DIR, { recursive: true });
    const names = (await fs.promises.readdir(PENDING_DIR)).filter(name => name.endsWith('.json')).sort();
    for (const name of names) {
      const source = path.join(PENDING_DIR, name);
      const processing = `${source}.processing`;
      try {
        await fs.promises.rename(source, processing);
        const job = JSON.parse(await fs.promises.readFile(processing, 'utf8'));
        if (job.chatid !== ALLOWED_CHAT_ID) throw new Error('Notification chat is not allowlisted');
        for (const filename of job.files) {
          const buffer = await fs.promises.readFile(filename);
          const uploaded = await client.uploadMedia(buffer, { type: 'file', filename: path.basename(filename) });
          await client.sendMediaMessage(ALLOWED_CHAT_ID, 'file', uploaded.media_id);
        }
        const report = await buildStatusReport(job);
        await client.sendMessage(ALLOWED_CHAT_ID, { msgtype: 'markdown', markdown: { content: report } });
        await fs.promises.rename(processing, path.join(SENT_DIR, name));
        console.log(JSON.stringify({ at: new Date().toISOString(), type: 'export_notification', job: job.id, files: job.files.length, sent: true }));
      } catch (error) {
        const failed = path.join(FAILED_DIR, name);
        await fs.promises.rename(processing, failed).catch(() => {});
        await fs.promises.writeFile(`${failed}.error.txt`, String(error?.stack || error)).catch(() => {});
        console.error('Export notification failed', error);
      }
    }
  } finally {
    queueBusy = false;
  }
}

setInterval(() => void processNotificationQueue(), 5000);

for (const signal of ['SIGINT', 'SIGTERM']) {
  process.on(signal, () => {
    client.disconnect();
    process.exit(0);
  });
}

