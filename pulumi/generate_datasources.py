import yaml
import os

password = os.environ.get('PROMETHEUS_PASSWORD')
if not password:
    raise SystemExit("Error: Set PROMETHEUS_PASSWORD environment variable before running this script")

datasources = {
    'apiVersion': 1,
    'datasources': [
        {
            'name': 'Prometheus',
            'type': 'prometheus',
            'access': 'proxy',
            'url': 'http://prometheus:9090',
            'basicAuth': True,
            'basicAuthUser': 'prometheus-admin',
            'basicAuthPassword': password,
            'isDefault': True
        },
        {
            'name': 'CloudWatch',
            'type': 'cloudwatch',
            'access': 'proxy',
            'jsonData': {
                'authType': 'default',
                'defaultRegion': 'ap-southeast-1'
            }
        }
    ]
}

config_map = {
    'apiVersion': 'v1',
    'kind': 'ConfigMap',
    'metadata': {'name': 'grafana-datasources', 'namespace': 'monitoring'},
    'data': {'datasources.yaml': yaml.dump(datasources)}
}

with open('/tmp/grafana-datasources-final.yaml', 'w') as f:
    yaml.dump(config_map, f)
