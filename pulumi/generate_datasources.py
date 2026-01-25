import yaml

password = 'ED($is6xAkAXg4o&nCWhJ%M)&=7Xh8!['

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
                'defaultRegion': 'us-east-1'
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
