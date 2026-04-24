const { execSync } = require('child_process');
const http = require('http');
const fs = require('fs');
const path = require('path');

const REGION = 'ap-southeast-1';
const MASTER_IP = process.argv[2] || execSync(
  `aws ec2 describe-instances --filters "Name=tag:Role,Values=k3s-master" "Name=instance-state-name,Values=running" --query "Reservations[0].Instances[0].PublicIpAddress" --output text --region ${REGION}`
).toString().trim();
const GRAFANA_BASE = { host: MASTER_IP, port: 30300 };
const GRAFANA_USER = 'admin';
let GRAFANA_PASS = 'Admin@123';
try {
  const kubecfg = path.join(__dirname, '../kubeconfig.yaml').replace(/\\/g, '/');
  if (fs.existsSync(kubecfg)) {
    const raw = execSync(
      `kubectl get secret grafana-admin-secret -n monitoring -o jsonpath={.data.admin-password} --kubeconfig "${kubecfg}"`,
      { stdio: ['pipe','pipe','pipe'] }
    ).toString().trim();
    if (raw) GRAFANA_PASS = Buffer.from(raw, 'base64').toString('utf8');
  }
} catch(e) {}
const DASHBOARD_DIR = path.join(__dirname, 'grafana-dashboards');

function req(method, path, body) {
  return new Promise((resolve, reject) => {
    const data = body ? JSON.stringify(body) : null;
    const auth = Buffer.from(`${GRAFANA_USER}:${GRAFANA_PASS}`).toString('base64');
    const opts = {
      ...GRAFANA_BASE, method, path,
      headers: {
        'Authorization': `Basic ${auth}`,
        'Content-Type': 'application/json',
        ...(data ? { 'Content-Length': Buffer.byteLength(data) } : {})
      }
    };
    const r = http.request(opts, res => {
      let d = '';
      res.on('data', c => d += c);
      res.on('end', () => { try { resolve(JSON.parse(d)); } catch { resolve(d); } });
    });
    r.on('error', reject);
    if (data) r.write(data);
    r.end();
  });
}

async function main() {
  // Get Prom creds via AWS CLI
  const raw = execSync(`aws secretsmanager get-secret-value --secret-id node-fleet/prometheus-auth --query SecretString --output text --region ${REGION}`).toString().trim();
  const { username: PROM_USER, password: PROM_PASS } = JSON.parse(raw);
  console.log('✓ Prom creds:', PROM_USER);

  // CloudWatch DS
  let cwDS = await req('GET', '/api/datasources/name/CloudWatch');
  if (!cwDS.id) {
    await req('POST', '/api/datasources', {
      name: 'CloudWatch', type: 'cloudwatch', access: 'proxy',
      jsonData: { authType: 'ec2_iam_role', defaultRegion: REGION }
    });
    cwDS = await req('GET', '/api/datasources/name/CloudWatch');
    console.log('✓ CloudWatch DS created');
  } else {
    console.log('✓ CloudWatch DS exists');
  }

  // Prometheus DS — update with basic auth
  let promDS = await req('GET', '/api/datasources/name/Prometheus');
  if (promDS.id) {
    await req('PUT', `/api/datasources/${promDS.id}`, {
      name: 'Prometheus', type: 'prometheus', access: 'proxy',
      url: `http://prometheus:9090`,
      basicAuth: true, basicAuthUser: PROM_USER,
      secureJsonData: { basicAuthPassword: PROM_PASS },
      jsonData: { httpMethod: 'POST' }
    });
    promDS = await req('GET', '/api/datasources/name/Prometheus');
    console.log('✓ Prometheus DS updated with auth');
  } else {
    await req('POST', '/api/datasources', {
      name: 'Prometheus', type: 'prometheus', access: 'proxy',
      url: `http://prometheus:9090`,
      basicAuth: true, basicAuthUser: PROM_USER,
      secureJsonData: { basicAuthPassword: PROM_PASS },
      jsonData: { httpMethod: 'POST' }
    });
    promDS = await req('GET', '/api/datasources/name/Prometheus');
    console.log('✓ Prometheus DS created');
  }

  const CW_UID = cwDS.uid;
  const PROM_UID = promDS.uid;
  console.log(`UIDs — CW: ${CW_UID}, Prom: ${PROM_UID}`);

  // Import dashboards
  // Get or create Node-Fleet folder
  let folderId = 0;
  const folders = await req('GET', '/api/folders');
  const nodeFleetFolder = (Array.isArray(folders) ? folders : []).find(f => f.title === 'Node-Fleet');
  if (nodeFleetFolder) {
    folderId = nodeFleetFolder.id;
  } else {
    await req('POST', '/api/folders', { title: 'Node-Fleet', uid: 'node-fleet-folder' });
    const updated = await req('GET', '/api/folders');
    folderId = ((Array.isArray(updated) ? updated : []).find(f => f.title === 'Node-Fleet') || {}).id || 0;
    console.log('✓ Created Node-Fleet folder');
  }
  console.log(`✓ Node-Fleet folder id: ${folderId}`);

  const files = fs.readdirSync(DASHBOARD_DIR).filter(f => f.endsWith('.json'));
  for (const f of files) {
    const dash = JSON.parse(fs.readFileSync(path.join(DASHBOARD_DIR, f), 'utf8'));
    if (dash.panels) {
      dash.panels.forEach(p => {
        (p.targets || []).forEach(t => {
          if (t.expr != null) t.datasource = { type: 'prometheus', uid: PROM_UID };
          else t.datasource = { type: 'cloudwatch', uid: CW_UID };
        });
        (p.panels || []).forEach(sp => {
          (sp.targets || []).forEach(t => {
            if (t.expr != null) t.datasource = { type: 'prometheus', uid: PROM_UID };
            else t.datasource = { type: 'cloudwatch', uid: CW_UID };
          });
        });
      });
    }
    delete dash.id;
    const resp = await req('POST', '/api/dashboards/db', { dashboard: dash, overwrite: true, folderId, message: 'fix-grafana' });
    const ok = resp.status === 'success';
    console.log(`${ok ? '✓' : '✗'} ${dash.title || f}: ${resp.status || resp.message || JSON.stringify(resp).slice(0,100)}`);
  }

  console.log(`\n✅ Done!\nGrafana: http://${MASTER_IP}:30300  login: admin / ${GRAFANA_PASS}`);
}

main().catch(e => { console.error('ERROR:', e.message); process.exit(1); });
