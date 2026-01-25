import yaml
import urllib.parse

password = 'ED($is6xAkAXg4o&nCWhJ%M)&=7Xh8!['
encoded_password = urllib.parse.quote(password)
user = "prometheus-admin"

# Note: URL auth structure: http://user:pass@host:port
url = f"http://{user}:{encoded_password}@prometheus:9090"

datasources = {
    'apiVersion': 1,
    'datasources': [
        {
            'name': 'Prometheus',
            'type': 'prometheus',
            'access': 'proxy',
            'url': url,
            'isDefault': True,
            'basicAuth': False
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

with open('/tmp/grafana-datasources-url-auth.yaml', 'w') as f:
    yaml.dump(config_map, f)
