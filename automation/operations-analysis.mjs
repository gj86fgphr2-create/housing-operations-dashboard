const DEFAULT_MODEL = 'gpt-5.6-luna';
const DEFAULT_TIMEOUT_MS = 20000;

const OUTPUT_SCHEMA = {
  type: 'object',
  additionalProperties: false,
  properties: {
    title: { type: 'string' },
    overview: { type: 'string' },
    alerts: { type: 'array', items: { type: 'string' }, maxItems: 3 },
    targetStatus: { type: 'string' },
    actions: { type: 'array', items: { type: 'string' }, maxItems: 3 },
  },
  required: ['title', 'overview', 'alerts', 'targetStatus', 'actions'],
};

const INSTRUCTIONS = `你是住房租赁运营分析助手。根据服务器提供的汇总JSON，生成简洁、审慎、可执行的中文运营简报。
规则：
1. 只能使用输入中已有的指标和预先计算结果，不得虚构、猜测或改写数字。
2. 新签减实际退租才是房源净变化；续租不计入净增量。
3. 优先说明综合在租率、近7天净变化、月度目标差距、空房与最低综合在租率项目。
4. 不要求也不得输出顾客姓名、手机号、合同号、房间号或其他个人信息。
5. 每条尽量一句话；预警和建议各不超过3条；无明显异常时明确写“暂无新增重大预警”。
6. 仅返回指定JSON结构，不要返回Markdown。`;

function integer(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? Math.round(parsed) : 0;
}

function percent(value) {
  const parsed = Number(value || 0);
  return Number.isFinite(parsed) ? parsed.toFixed(1) : '0.0';
}

function cleanLine(value) {
  return String(value || '').replace(/[\r\n]+/g, ' ').trim();
}

function validateAnalysis(value) {
  if (!value || typeof value !== 'object') throw new Error('analysis is not an object');
  for (const key of ['title', 'overview', 'targetStatus']) {
    if (typeof value[key] !== 'string' || !value[key].trim()) throw new Error(`missing ${key}`);
  }
  for (const key of ['alerts', 'actions']) {
    if (!Array.isArray(value[key]) || value[key].some(item => typeof item !== 'string')) {
      throw new Error(`invalid ${key}`);
    }
  }
  return {
    title: cleanLine(value.title),
    overview: cleanLine(value.overview),
    alerts: value.alerts.slice(0, 3).map(cleanLine).filter(Boolean),
    targetStatus: cleanLine(value.targetStatus),
    actions: value.actions.slice(0, 3).map(cleanLine).filter(Boolean),
  };
}

function extractOutputText(response) {
  for (const item of response?.output || []) {
    if (item?.type !== 'message') continue;
    for (const content of item.content || []) {
      if (content?.type === 'output_text' && content.text) return content.text;
    }
  }
  if (typeof response?.output_text === 'string') return response.output_text;
  throw new Error('OpenAI response did not contain output text');
}

export function buildDeterministicAnalysis(input = {}) {
  const occupancy = input.occupancy || {};
  const recent = input.performance?.recent7Days || {};
  const monthly = input.targets?.monthly || {};
  const currentPeriod = input.targets?.currentPeriod;
  const projects = input.lowestComprehensiveProjects || [];
  const netChange = integer(recent.netChange);
  const alerts = [];

  if (netChange < 0) {
    alerts.push(`近7天新签${integer(recent.new)}份、实际退租${integer(recent.actualCheckout)}份，净减少${Math.abs(netChange)}份。`);
  } else if (netChange > 0) {
    alerts.push(`近7天新签${integer(recent.new)}份、实际退租${integer(recent.actualCheckout)}份，净增加${netChange}份。`);
  } else {
    alerts.push(`近7天新签与实际退租相抵，净变化为0份。`);
  }
  if (integer(occupancy.unqualifiedUnrentable) > 0) {
    alerts.push(`不可租空房中有${integer(occupancy.unqualifiedUnrentable)}套不属于已预订或将搬入，建议核查原因。`);
  }
  if (projects[0] && Number(projects[0].comprehensiveRatePct) < 90) {
    alerts.push(`${cleanLine(projects[0].name)}综合在租率${percent(projects[0].comprehensiveRatePct)}%，为当前较低项目。`);
  }
  if (!alerts.length) alerts.push('暂无新增重大预警。');

  const actions = [];
  if (netChange < 0) actions.push('优先跟进近期到期合同续租，并按退租量补足新签。');
  if (integer(occupancy.rentableVacant) > 0) actions.push(`集中推广${integer(occupancy.rentableVacant)}套可租空房，优先处理空置时间较长房源。`);
  if (projects[0]) actions.push(`复盘${cleanLine(projects[0].name)}的空置、锁房和出租转化。`);

  const periodText = currentPeriod
    ? `；${cleanLine(currentPeriod.period)}新签${integer(currentPeriod.newActual)}/${integer(currentPeriod.newTarget)}、续租${integer(currentPeriod.renewalActual)}/${integer(currentPeriod.renewalTarget)}`
    : '';
  return {
    title: `运营分析（${cleanLine(input.dataDate) || '当前'}）`,
    overview: `综合在租${integer(occupancy.comprehensive)}套，在租率${percent(occupancy.comprehensiveRatePct)}%；空房${integer(occupancy.vacant)}套，其中可租${integer(occupancy.rentableVacant)}套、不可租${integer(occupancy.unrentableVacant)}套。`,
    alerts: alerts.slice(0, 3),
    targetStatus: `本月新签${integer(monthly.newActual)}/${integer(monthly.newTarget)}，还差${integer(monthly.newRemaining)}；续租${integer(monthly.renewalActual)}/${integer(monthly.renewalTarget)}，还差${integer(monthly.renewalRemaining)}${periodText}。`,
    actions: actions.slice(0, 3),
  };
}

async function callOpenAI(input, { apiKey, model, timeoutMs, fetchImpl }) {
  const response = await fetchImpl('https://api.openai.com/v1/responses', {
    method: 'POST',
    headers: {
      authorization: `Bearer ${apiKey}`,
      'content-type': 'application/json',
    },
    body: JSON.stringify({
      model,
      instructions: INSTRUCTIONS,
      input: JSON.stringify(input),
      reasoning: { effort: 'low' },
      text: {
        verbosity: 'low',
        format: {
          type: 'json_schema',
          name: 'housing_operations_analysis',
          strict: true,
          schema: OUTPUT_SCHEMA,
        },
      },
      max_output_tokens: 900,
      store: false,
    }),
    signal: AbortSignal.timeout(timeoutMs),
  });
  const body = await response.text();
  if (!response.ok) throw new Error(`OpenAI HTTP ${response.status}: ${body.slice(0, 300)}`);
  const payload = JSON.parse(body);
  const analysis = validateAnalysis(JSON.parse(extractOutputText(payload)));
  return {
    analysis,
    responseId: payload.id || '',
    usage: payload.usage || null,
  };
}

export async function generateOperationsAnalysis(input, options = {}) {
  const fallback = buildDeterministicAnalysis(input);
  const apiKey = options.apiKey || '';
  const model = options.model || DEFAULT_MODEL;
  const timeoutMs = Number(options.timeoutMs || DEFAULT_TIMEOUT_MS);
  const fetchImpl = options.fetchImpl || globalThis.fetch;
  if (!apiKey) {
    return { analysis: fallback, source: 'rules', model: '', responseId: '', usage: null, error: 'OPENAI_API_KEY未配置' };
  }

  let lastError;
  for (let attempt = 1; attempt <= 2; attempt += 1) {
    try {
      const result = await callOpenAI(input, { apiKey, model, timeoutMs, fetchImpl });
      return { ...result, source: 'openai', model, error: '' };
    } catch (error) {
      lastError = error;
      if (attempt < 2) await new Promise(resolve => setTimeout(resolve, 1000));
    }
  }
  return {
    analysis: fallback,
    source: 'rules',
    model,
    responseId: '',
    usage: null,
    error: cleanLine(lastError?.message || lastError).slice(0, 300),
  };
}

export function renderAnalysisMarkdown(result, dashboardUrl = '') {
  const item = result.analysis;
  const sourceLabel = result.source === 'openai' ? 'GPT辅助分析' : '规则分析';
  const lines = [
    `**${item.title}**`,
    `> 分析方式：${sourceLabel}`,
    '',
    `**经营概况**`,
    `> ${item.overview}`,
    '',
    `**预警**`,
    ...item.alerts.map(alert => `> • ${alert}`),
    '',
    `**目标进度**`,
    `> ${item.targetStatus}`,
    '',
    `**建议动作**`,
    ...item.actions.map((action, index) => `> ${index + 1}. ${action}`),
  ];
  if (dashboardUrl) lines.push('', `[查看最新在线工作台](${dashboardUrl})`);
  return lines.join('\n');
}
