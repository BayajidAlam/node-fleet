# VS Code Agent Files (.agent.md)

These `.agent.md` files enable **VS Code Copilot to route work to specialized agents** through auto-selection or manual invocation.

## 📁 Agent Files

| File                                           | Triggers When Working On                            | Purpose                                                                               |
| ---------------------------------------------- | --------------------------------------------------- | ------------------------------------------------------------------------------------- |
| [app-agent.agent.md](app-agent.agent.md)       | `lambda/**`, `demo-app/**`, `tests/**`              | Lambda Python autoscaler code — scaling logic, EC2 management, metrics, DynamoDB lock |
| [infra-agent.agent.md](infra-agent.agent.md)   | `pulumi/**`, `k3s/**`, `gitops/**`, `monitoring/**` | Pulumi TypeScript IaC, K3s scripts, Prometheus/Grafana config                         |
| [review-agent.agent.md](review-agent.agent.md) | `**/*.py`, `**/*.ts`, `**/*.yaml`, `**/*.sh`        | Read-only compliance review; checks SmartScale requirements, security, drain logic    |
| [docs-agent.agent.md](docs-agent.agent.md)     | Manual only: `@docs-agent`                          | Generates runbooks, architecture docs, README updates, compliance tables              |

## 🚀 How It Works

### Auto-Selection (Recommended)

VS Code Copilot automatically picks the right agent when you:

- Open a file in `lambda/` → App Agent activates
- Edit `pulumi/src/*.ts` → Infra Agent activates
- Open any `.py`, `.ts`, `.yaml` file → Review Agent can be manually selected

### Manual Selection

In Copilot Chat, type:

```
@app-agent fix the drain validation in ec2_manager.py
@infra-agent add a CloudWatch alarm for scaling failures
@review-agent check compliance against SmartScale requirements
@docs-agent create a scale-down runbook
```

## 📋 YAML Frontmatter Explained

Each `.agent.md` file starts with metadata:

```yaml
---
name: app-agent
description: node-fleet Application Agent
applyTo:
  - "lambda/**" # Auto-activate when editing files matching these patterns
preferredTools:
  - read_file # Tools this agent should use
  - replace_string_in_file
avoidTools:
  - pulumi # Tools this agent should avoid
ignorePatterns:
  - "pulumi/**" # Files this agent shouldn't touch
---
```

## 🔧 Editing Agents

To modify agent behavior:

1. Edit the `.agent.md` file
2. Change `applyTo` patterns to adjust auto-activation
3. Update rules in the "Mandatory Rules" section
4. Reload VS Code window (Ctrl+Shift+P → "Developer: Reload Window")

## 🔄 Relationship to Other Configs

| Config                            | Purpose                               | When to Use                                           |
| --------------------------------- | ------------------------------------- | ----------------------------------------------------- |
| `.github/copilot-instructions.md` | Global Copilot project context        | Auto-loaded by Copilot in every session               |
| `.agents/CONTEXT.md`              | Single source of truth for all agents | Read by every agent before working                    |
| `.agents/*.agent.md`              | Specialized agents (auto or manual)   | Auto-activates by file match or manual `@agent` usage |

All agent files reference the shared [CONTEXT.md](CONTEXT.md) for consistency.

## 📚 References

- [VS Code Agent Customization Docs](https://code.visualstudio.com/docs/copilot/copilot-customization)
- [node-fleet CONTEXT.md](CONTEXT.md)

---

**Ready to use!** Open any file in `lambda/` and Copilot will automatically load the App Agent context.
