import os
import json

dashboard_dir = "/home/bayajidswe/My-files/poridhi-project/node-fleet/monitoring/grafana-dashboards"
output_file = "grafana-dashboards.yaml"

cm_header = """apiVersion: v1
kind: ConfigMap
metadata:
  name: grafana-dashboards
  namespace: monitoring
data:
"""

with open(output_file, "w") as out:
    out.write(cm_header)
    for filename in os.listdir(dashboard_dir):
        if filename.endswith(".json"):
            with open(os.path.join(dashboard_dir, filename), "r") as f:
                content = f.read()
                # Indent content by 2 spaces
                indented_content = "\n".join(["  " + line for line in content.splitlines()])
                out.write(f"  {filename}: |\n{indented_content}\n")

print("Generated grafana-dashboards.yaml")
